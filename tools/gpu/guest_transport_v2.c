/* One-shot diagnostic transport, not a persistent GPU/input multiplexer.
 * The disposable image's input service launches this supervisor. It owns the
 * console only while the worker runs, then execs the ORIGINAL input helper.
 * The worker receives pipes, never /dev/console, and cannot publish a GPU.
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

extern char **environ;
enum { CHUNK = 48, LINE = 160 };
static double now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) return -1;
    return ts.tv_sec + ts.tv_nsec / 1e9;
}
static bool all(int fd, const void *buf, size_t size) {
    const unsigned char *p = buf;
    while (size) {
        ssize_t n = write(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n; size -= (size_t)n;
    }
    return true;
}
#include "uart_link.h"
static bool emit(int fd,const LinkFrame *frame) {
    char bytes[LINK_MAX];unsigned n=link_encode(frame,bytes);return n && all(fd,bytes,n);
}
/* One outstanding frame per direction. Retries repeat bytes, never append
 * them twice to the worker. State is discarded after disconnect/deadline.
 */
/* /dev/console is not a socket. Use its blocking read contract and poll a
 * pipe instead. On success it stops after the protected final ACK, before
 * another blocking console read; exec cannot be relied on to interrupt an
 * arbitrary guest console driver read. */
struct ConsoleReader {
    int console, writer;
    uint32_t session;
    uint64_t close_offset;
    atomic_bool closing;
};
static void *console_reader(void *opaque) {
    struct ConsoleReader *r=opaque;
    LinkParser parser={0};
    unsigned char bytes[512];
    for(;;) {
        ssize_t n=read(r->console,bytes,sizeof(bytes));
        if(n<0 && errno==EINTR)continue;
        if(n<=0 || !all(r->writer,bytes,(size_t)n))break;
        bool final_ack=false;
        for(ssize_t i=0;i<n;i++) {
            LinkFrame f;
            if(link_feed(&parser,bytes[i],&f) &&
               atomic_load_explicit(&r->closing,memory_order_acquire) &&
               f.kind=='c' && !f.length && f.session==r->session &&
               f.offset==r->close_offset)final_ack=true;
        }
        if(final_ack)break;
    }
    close(r->writer);return NULL;
}
static const char *transport_loop(int console,int incoming,int input,int output,pid_t child,int *status,struct ConsoleReader *reader) {
    LinkParser parser={0};LinkFrame tx={.session=arc4random(),.kind='R'},last={0};
    uint64_t sent=0,received=0,duplicates=0,retries=0;
    bool pending=true,handshake=false,closing=false;unsigned attempts=0;
    double next=0,deadline=now()+900;
    while(now()>=0 && now()<deadline) {
        if(pending && now()>=next) {
            if(attempts++>=80)return "ack-deadline";
            if(attempts>1)retries++;
            if(!emit(console,&tx))return "console-output-error";
            next=now()+.5;
        }
        struct pollfd fds[]={{incoming,POLLIN,0},{handshake&&!pending?output:-1,POLLIN,0}};
        int n=poll(fds,2,10);
        if(n<0){if(errno==EINTR)continue;return "poll-error";}
        if(fds[0].revents&(POLLERR|POLLNVAL))return "console-pipe-error";
        if(fds[0].revents&(POLLIN|POLLHUP)) {
            unsigned char bytes[512];ssize_t count=read(incoming,bytes,sizeof(bytes));
            if(count<=0)return "console-closed";
            for(ssize_t i=0;i<count;i++) {
                LinkFrame f;if(!link_feed(&parser,bytes[i],&f))continue;
                if(f.session!=tx.session)continue;
                if(f.kind=='r' && !f.length && !f.offset && pending && tx.kind=='R') {pending=false;handshake=true;attempts=0;}
                else if(f.kind=='g' && !f.length && pending && tx.kind=='G' && f.offset==sent+tx.length) {sent=f.offset;pending=false;attempts=0;}
                else if(f.kind=='c' && !f.length && closing && f.offset==sent) {
                    char log[200];int used=snprintf(log,sizeof(log),"DVMGPU_LINK received=%llu sent=%llu retries=%llu duplicates=%llu rejected=%llu\n",(unsigned long long)received,(unsigned long long)sent,(unsigned long long)retries,(unsigned long long)duplicates,(unsigned long long)parser.rejected);
                    all(console,log,(size_t)used);return "complete";
                } else if(f.kind=='H' && handshake && f.length && f.offset<=UINT64_MAX-f.length) {
                    if(f.offset==received && !closing) {
                        ssize_t wrote=write(input,f.data,f.length);
                        if(wrote<0 && (errno==EAGAIN||errno==EINTR))continue;
                        if(wrote!=(ssize_t)f.length)return "worker-input-error";
                        received+=f.length;last=f;
                    } else if(f.offset==last.offset && f.length==last.length && !memcmp(f.data,last.data,f.length))duplicates++;
                    else return "response-offset-or-content-error";
                    LinkFrame ack={.session=tx.session,.kind='h',.offset=received};
                    if(!emit(console,&ack))return "ack-output-error";
                }
            }
        }
        if(handshake && !pending && !closing && (fds[1].revents&(POLLIN|POLLHUP))) {
            ssize_t count=read(output,tx.data,sizeof(tx.data));
            if(count>0) {tx.kind='G';tx.offset=sent;tx.length=(unsigned)count;pending=true;attempts=0;next=0;}
            else if(count==0) {
                pid_t reaped=waitpid(child,status,WNOHANG);
                for(unsigned i=0;reaped==0 && i<20;i++){poll(NULL,0,100);reaped=waitpid(child,status,WNOHANG);}
                if(reaped!=child)return "worker-exit-deadline";
                /* C acknowledges all consumed response bytes and child exit.
                 * The host can retire a lost final h ACK from this witness. */
                tx.kind='C';tx.offset=sent;tx.length=12;
                for(unsigned i=0;i<8;i++)tx.data[i]=(unsigned char)(received>>(56-8*i));
                for(unsigned i=0;i<4;i++)tx.data[8+i]=(unsigned char)((uint32_t)*status>>(24-8*i));
                closing=true;pending=true;attempts=0;next=0;
                reader->session=tx.session;reader->close_offset=sent;
                atomic_store_explicit(&reader->closing,true,memory_order_release);
            } else if(errno!=EAGAIN && errno!=EINTR)return "worker-output-error";
        }
    }
    return "worker-deadline";
}
static int fallback(void) {
    execl("/usr/local/libexec/dvm-input","dvm-input",(char *)NULL);
    return 4;
}
int main(int argc,char **args) {
    (void)argc;(void)args;
    bool host_test=false;
#ifdef DVM_HOST_TEST
    host_test=argc==6 && !strcmp(args[1],"--host-test");
    if(!host_test)return 2;
#endif
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, SIG_DFL); signal(SIGINT, SIG_DFL);
    sigset_t unblocked; sigemptyset(&unblocked); sigprocmask(SIG_SETMASK,&unblocked,NULL);
    int lock=-1,console=-1;
    if(!host_test) {
    lock=open("/var/run/dvm-input.lock",O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if (lock<0 || flock(lock,LOCK_EX|LOCK_NB)) return 1;
    int once=open("/var/run/dvm-gpu-diagnostic.once",O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);
    if (once<0) {close(lock);return fallback();}
    close(once);
    console=open("/dev/console",O_RDWR|O_NOCTTY|O_CLOEXEC);
    if (console<0) { close(lock); return fallback(); }
    }
#ifdef DVM_HOST_TEST
    else console=atoi(args[2]);
    if(host_test)fcntl(console,F_SETFD,FD_CLOEXEC);
#endif
    struct termios old, raw; bool termios_ok=tcgetattr(console,&old)==0;
    if (termios_ok) {
        raw=old;cfmakeraw(&raw);raw.c_cc[VMIN]=1;raw.c_cc[VTIME]=0;
        if (tcsetattr(console,TCSANOW,&raw)) {close(console);close(lock);return fallback();}
    }
    int input[2]={-1,-1}, output[2]={-1,-1}, incoming[2]={-1,-1};
    struct ConsoleReader reader={0};
    atomic_init(&reader.closing,false);
    pid_t child=-1;
    int child_status=-1;
    const char *reason="setup-error";
    if (pipe(input) || pipe(output) || pipe(incoming)) goto done;
    for (unsigned i=0;i<2;i++) {fcntl(input[i],F_SETFD,FD_CLOEXEC);fcntl(output[i],F_SETFD,FD_CLOEXEC);fcntl(incoming[i],F_SETFD,FD_CLOEXEC);}
    posix_spawn_file_actions_t actions;
    if (posix_spawn_file_actions_init(&actions)) goto done;
    if(posix_spawn_file_actions_adddup2(&actions,input[0],STDIN_FILENO) ||
       posix_spawn_file_actions_adddup2(&actions,output[1],STDOUT_FILENO) ||
       posix_spawn_file_actions_adddup2(&actions,console,STDERR_FILENO)) {
        posix_spawn_file_actions_destroy(&actions);goto done;
    }
    setenv("DVM_PROXY_FIRST_DELAY_MS","200",1);
    setenv("DVM_PROXY_LIBRARY_REF","1",1);
    setenv("DVM_PROXY_REPORT","1",1);
    char *argv[]={"/usr/local/libexec/dvm-gpu-work", "--stdio",
        "/usr/local/libexec/DVMForward.bundle/DVMForward",
        "/System/Library/Frameworks/QuartzCore.framework/default.metallib", NULL};
#ifdef DVM_HOST_TEST
    argv[0]=args[3];argv[2]=args[4];argv[3]=args[5];
#endif
    int spawn_error=posix_spawn(&child,argv[0],&actions,NULL,argv,environ);
    posix_spawn_file_actions_destroy(&actions);
    if (spawn_error) { errno=spawn_error;goto done; }
    close(input[0]);input[0]=-1;close(output[1]);output[1]=-1;
    fcntl(input[1],F_SETFL,O_NONBLOCK);fcntl(output[0],F_SETFL,O_NONBLOCK);
    reader.console=console;reader.writer=incoming[1];
    pthread_t reader_thread;
    if(pthread_create(&reader_thread,NULL,console_reader,&reader))goto done;
    incoming[1]=-1; /* owned by reader */
    reason=transport_loop(console,incoming[0],input[1],output[0],child,&child_status,&reader);
    if(!strcmp(reason,"complete")) {
        if(pthread_join(reader_thread,NULL))reason="console-reader-join-error";
        else all(console,"DVMGPU_READER_STOPPED final_ack=1\n",sizeof("DVMGPU_READER_STOPPED final_ack=1\n")-1);
    } else pthread_detach(reader_thread);
done:
    for(unsigned i=0;i<2;i++){if(input[i]>=0)close(input[i]);if(output[i]>=0)close(output[i]);if(incoming[i]>=0)close(incoming[i]);}
    if(child>0 && child_status<0) {
        pid_t reaped=waitpid(child,&child_status,WNOHANG);
        if(reaped==0){kill(child,SIGKILL);while(waitpid(child,&child_status,0)<0 && errno==EINTR){}}
    }
    char final[160];int n=snprintf(final,sizeof(final),"DVMGPU_DONE reason=%s wait_status=%d fallback=original-input\n",reason,child_status);
    all(console,final,(size_t)n);
    if(termios_ok)tcsetattr(console,TCSANOW,&old);
    close(console);if(lock>=0)close(lock);
    if(host_test)return !strcmp(reason,"complete") && child_status==0?0:1;
    return fallback();
}
