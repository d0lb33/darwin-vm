/* Diagnostic UART v2: CRC32-protected ASCII frames, bounded delimiter resync.
 * CRC covers session, kind, stream offset, length and payload. It detects
 * accidental console noise; this is not an authentication protocol.
 */
#ifndef DVM_UART_LINK_H
#define DVM_UART_LINK_H
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <inttypes.h>
enum { LINK_CHUNK=128, LINK_MAX=384 };
typedef struct { uint32_t session; char kind; uint64_t offset; unsigned length; unsigned char data[LINK_CHUNK]; } LinkFrame;
typedef struct { char bytes[LINK_MAX]; unsigned used; bool overflow; uint64_t rejected; } LinkParser;
static uint32_t link_crc(const void *data,size_t n) {
    uint32_t c=~0u; const unsigned char *p=data;
    while(n--) {c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);}
    return ~c;
}
static int link_hex(char c) {return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;}
static bool link_number(const char *p,unsigned n,uint64_t *value) {
    uint64_t v=0;for(unsigned i=0;i<n;i++){int d=link_hex(p[i]);if(d<0)return false;v=(v<<4)|(unsigned)d;}*value=v;return true;
}
static unsigned link_encode(const LinkFrame *f,char out[LINK_MAX]) {
    if(f->length>LINK_CHUNK)return 0;
    int n=snprintf(out,LINK_MAX,"~D2:%08" PRIx32 ":%c:%016" PRIx64 ":%04x:",f->session,f->kind,f->offset,f->length);
    static const char hex[]="0123456789abcdef";
    for(unsigned i=0;i<f->length;i++){out[n++]=hex[f->data[i]>>4];out[n++]=hex[f->data[i]&15];}
    uint32_t crc=link_crc(out+1,(size_t)n-1);
    n+=snprintf(out+n,LINK_MAX-(unsigned)n,":%08" PRIx32 "~\n",crc);
    return (unsigned)n;
}
static bool link_decode(const char *p,unsigned n,LinkFrame *f) {
    /* D2:ssssssss:k:oooooooooooooooo:llll:<hex>:cccccccc */
    if(n<45 || memcmp(p,"D2:",3) || p[11]!=':' || p[13]!=':' || p[30]!=':' || p[35]!=':')return false;
    uint64_t session,offset,length,crc;
    if(!link_number(p+3,8,&session)||!link_number(p+14,16,&offset)||!link_number(p+31,4,&length)||length>LINK_CHUNK)return false;
    unsigned end=36+(unsigned)length*2;
    if(n!=end+9 || p[end]!=':' || !link_number(p+end+1,8,&crc) || link_crc(p,end)!=(uint32_t)crc)return false;
    for(unsigned i=0;i<length;i++){uint64_t byte;if(!link_number(p+36+2*i,2,&byte))return false;f->data[i]=(unsigned char)byte;}
    f->session=(uint32_t)session;f->kind=p[12];f->offset=offset;f->length=(unsigned)length;
    return f->kind && strchr("RrGgHhCc",f->kind)!=NULL;
}
static bool link_feed(LinkParser *p,unsigned char c,LinkFrame *frame) {
    if(c=='~') {
        bool okay=!p->overflow && link_decode(p->bytes,p->used,frame);
        if(p->used>=3 && !memcmp(p->bytes,"D2:",3) && !okay)p->rejected++;
        p->used=0;p->overflow=false;return okay;
    }
    if(p->used<LINK_MAX && !p->overflow)p->bytes[p->used++]=(char)c;
    else p->overflow=true;
    return false;
}
#endif
