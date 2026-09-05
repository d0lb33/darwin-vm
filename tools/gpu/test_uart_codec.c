#include "uart_link.h"
#include <assert.h>
int main(void) {
    LinkFrame f={.session=0x12345678,.kind='G',.offset=0x100,.length=128},decoded;
    for(unsigned i=0;i<f.length;i++)f.data[i]=(unsigned char)i;
    char wire[LINK_MAX];unsigned n=link_encode(&f,wire);
    LinkParser parser={0};unsigned accepted=0;
    for(unsigned repeat=0;repeat<2;repeat++)for(unsigned i=0;i<n;i++)if(link_feed(&parser,wire[i],&decoded))accepted++;
    assert(accepted==2 && parser.rejected==0 && decoded.session==f.session && decoded.kind=='G' && decoded.offset==f.offset && decoded.length==128 && !memcmp(decoded.data,f.data,128));
    for(unsigned corrupt=1;corrupt<n-2;corrupt++) {
        LinkParser bad={0};unsigned count=0;
        for(unsigned i=0;i<n;i++)if(link_feed(&bad,wire[i]^(i==corrupt?1:0),&decoded))count++;
        for(unsigned i=0;i<n;i++)if(link_feed(&bad,wire[i],&decoded))count++;
        assert(count==1); /* Corrupt frame rejected; following retry resyncs. */
    }
    puts("PASS CRC mutation at every body byte, resync, max payload, clean-frame rejection count=0");
    fwrite(wire,1,n,stdout);return 0;
}
