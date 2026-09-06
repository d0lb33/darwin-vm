/* One-shot diagnostic transport, not a persistent GPU/input multiplexer.
 * The disposable image's input service launches this supervisor. It owns the
 * console only while the worker runs, then execs the ORIGINAL input helper.
 * The worker receives pipes, never /dev/console, and cannot publish a GPU.
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdint.h>
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
static int nibble(char c) {
    if (c >= '0' && c <= '9') return c-'0';
    if (c >= 'a' && c <= 'f') return c-'a'+10;
    return -1;
}
/* Byte-stream offsets prevent duplicate or missing response bytes. Input
 * frames are acknowledged only after one atomic <=PIPE_BUF write to worker.
 */
static bool incoming(char *line, uint64_t *expected, int worker, int console) {
    if (strncmp(line,"DVMGPU_IN ",10)) return false;
    const char *p=line+10;
    uint64_t offset=0;
    if (*p<'0' || *p>'9') return false;
    while (*p>='0' && *p<='9') {
        unsigned digit=(unsigned)(*p++-'0');
        if (offset>(UINT64_MAX-digit)/10) return false;
        offset=offset*10+digit;
    }
    if (*p++!=' ' || offset!=*expected) return false;
    const char *hex=p;
    size_t length = strlen(hex);
    if (!length || length%2 || length > CHUNK*2 || *expected>UINT64_MAX-length/2) return false;
    unsigned char bytes[CHUNK];
    for (size_t i=0; i<length; i+=2) {
        int hi=nibble(hex[i]), lo=nibble(hex[i+1]);
        if (hi<0 || lo<0) return false;
        bytes[i/2]=(unsigned char)(hi*16+lo);
    }
    ssize_t n=write(worker, bytes, length/2);
    if (n != (ssize_t)(length/2)) return false;
    *expected += length/2;
    char ack[64];
    int used=snprintf(ack,sizeof(ack),"DVMGPU_ACK %llu\n",(unsigned long long)*expected);
    return all(console,ack,(size_t)used);
}
static int fallback(void) {
    execl("/usr/local/libexec/dvm-input","dvm-input",(char *)NULL);
    return 4;
}
int main(void) {
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, SIG_DFL); signal(SIGINT, SIG_DFL);
    sigset_t unblocked; sigemptyset(&unblocked); sigprocmask(SIG_SETMASK,&unblocked,NULL);
    int lock=open("/var/run/dvm-input.lock",O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if (lock<0 || flock(lock,LOCK_EX|LOCK_NB)) return 1;
    int once=open("/var/run/dvm-gpu-diagnostic.once",O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);
    if (once<0) {close(lock);return fallback();}
    close(once);
    int console=open("/dev/console",O_RDWR|O_NOCTTY|O_CLOEXEC);
    if (console<0) { close(lock); return fallback(); }
    struct termios old, raw; bool termios_ok=tcgetattr(console,&old)==0;
    if (termios_ok) {
        raw=old;cfmakeraw(&raw);raw.c_cc[VMIN]=1;raw.c_cc[VTIME]=0;
        if (tcsetattr(console,TCSANOW,&raw)) {close(console);close(lock);return fallback();}
    }
    int input[2]={-1,-1}, output[2]={-1,-1};
    pid_t child=-1;
    int child_status=-1;
    const char *reason="setup-error";
    if (pipe(input) || pipe(output)) goto done;
    for (unsigned i=0;i<2;i++) {fcntl(input[i],F_SETFD,FD_CLOEXEC);fcntl(output[i],F_SETFD,FD_CLOEXEC);}
    posix_spawn_file_actions_t actions;
    if (posix_spawn_file_actions_init(&actions)) goto done;
    if(posix_spawn_file_actions_adddup2(&actions,input[0],STDIN_FILENO) ||
       posix_spawn_file_actions_adddup2(&actions,output[1],STDOUT_FILENO) ||
       posix_spawn_file_actions_adddup2(&actions,console,STDERR_FILENO)) {
        posix_spawn_file_actions_destroy(&actions);goto done;
    }
    setenv("DVM_PROXY_FIRST_DELAY_MS","200",1);
    char *argv[]={"/usr/local/libexec/dvm-gpu-work", "--stdio",
        "/usr/local/libexec/DVMForward.bundle/DVMForward",
        "/System/Library/Frameworks/QuartzCore.framework/default.metallib", NULL};
    int spawn_error=posix_spawn(&child,argv[0],&actions,NULL,argv,environ);
    posix_spawn_file_actions_destroy(&actions);
    if (spawn_error) { errno=spawn_error;goto done; }
    close(input[0]);input[0]=-1;close(output[1]);output[1]=-1;
    fcntl(input[1],F_SETFL,O_NONBLOCK);fcntl(output[0],F_SETFL,O_NONBLOCK);
    all(console,"DVMGPU_READY\n",13);
    uint64_t sent=0, received=0;
    char line[LINE];size_t used=0;
    double deadline=now()+900;
    reason="worker-deadline";
    while (now()>=0 && now()<deadline) {
        struct pollfd fds[]={{console,POLLIN,0},{output[0],POLLIN,0}};
        int ready=poll(fds,2,100);
        if (ready<0) { if(errno==EINTR)continue;reason="poll-error";break; }
        if (fds[1].revents & (POLLIN|POLLHUP)) {
            unsigned char bytes[CHUNK];ssize_t n=read(output[0],bytes,sizeof(bytes));
            if (n>0) {
                char record[160];int at=snprintf(record,sizeof(record),"DVMGPU_OUT %llu ",(unsigned long long)sent);
                static const char h[]="0123456789abcdef";
                for(ssize_t i=0;i<n;i++){record[at++]=h[bytes[i]>>4];record[at++]=h[bytes[i]&15];}
                record[at++]='\n';
                if(!all(console,record,(size_t)at)){reason="console-output-error";break;}
                sent+=(uint64_t)n;
            } else if(n==0) {reason="worker-output-closed";break;}
            else if(errno!=EAGAIN && errno!=EINTR){reason="worker-read-error";break;}
        }
        if(fds[0].revents & POLLIN) {
            unsigned char c;ssize_t n=read(console,&c,1);
            if(n==1) {
                if(c=='\r')continue;
                if(c=='\n') {
                    line[used]=0;
                    if(used && !incoming(line,&received,input[1],console)){reason="bad-response-frame";break;}
                    used=0;
                } else if(c==0){reason="nul-response-frame";break;}
                else if(used+1<sizeof(line))line[used++]=(char)c;
                else {reason="overlong-response-frame";break;}
            }
        }
    }
done:
    for(unsigned i=0;i<2;i++){if(input[i]>=0)close(input[i]);if(output[i]>=0)close(output[i]);}
    if(child>0) {
        pid_t reaped=waitpid(child,&child_status,WNOHANG);
        if(reaped==0 && !strcmp(reason,"worker-output-closed")) {
            /* stdout can close just before exit; allow normal teardown. */
            for(unsigned i=0;i<20 && reaped==0;i++) {
                poll(NULL,0,100);reaped=waitpid(child,&child_status,WNOHANG);
            }
        }
        if(reaped==0) {kill(child,SIGKILL);while(waitpid(child,&child_status,0)<0 && errno==EINTR){}}
    }
    char final[160];int n=snprintf(final,sizeof(final),"DVMGPU_DONE reason=%s wait_status=%d fallback=original-input\n",reason,child_status);
    all(console,final,(size_t)n);
    if(termios_ok)tcsetattr(console,TCSANOW,&old);
    close(console);close(lock);
    /* Exactly the existing helper and event implementation, unchanged. */
    return fallback();
}
