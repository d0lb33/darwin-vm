/* Standalone tests of the same registry engine compiled into QEMU. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "xnu/dvm_surface_registry.h"
static uint64_t page(unsigned i){return DVM_SURFACE_DRAM_BASE+(uint64_t)i*DVM_SURFACE_PAGE;}
static uint64_t add(DVMSurfaceRegistry *s,unsigned first,uint64_t length,uint64_t offset){
    s->next_offset=offset;assert(!dvm_surface_begin(s,length));
    unsigned count=s->records[s->pending-1].expected;
    for(unsigned i=0;i<count;i++)assert(!dvm_surface_page(s,page(first+i)));
    assert(dvm_surface_ready(s));dvm_surface_activate(s);return s->last_id;
}
int main(void){
    DVMSurfaceRegistry *s=calloc(1,sizeof(*s));assert(s);
    assert(dvm_surface_begin(s,0)==DVM_SURFACE_ARGUMENT);
    assert(dvm_surface_begin(s,UINT64_MAX)==DVM_SURFACE_ARGUMENT);
    s->next_offset=16384;assert(dvm_surface_begin(s,1)==DVM_SURFACE_ARGUMENT);
    s->next_offset=0;assert(!dvm_surface_begin(s,16385));
    assert(!dvm_surface_page(s,page(0)));assert(!dvm_surface_ready(s));
    assert(s->status==DVM_SURFACE_ARGUMENT);dvm_surface_abort(s);assert(!s->owners[0]);
    assert(!dvm_surface_begin(s,16384));assert(dvm_surface_page(s,page(0)+1)==DVM_SURFACE_RANGE);dvm_surface_abort(s);
    assert(!dvm_surface_begin(s,32768));assert(!dvm_surface_page(s,page(0)));
    assert(dvm_surface_page(s,page(0))==DVM_SURFACE_ALIAS);dvm_surface_abort(s);assert(!s->owners[0]);
    uint64_t first=add(s,0,24211456,0);assert(s->active_bytes==1478*16384ull);
    assert(dvm_surface_find(s,first)->count==1478);
    assert(!dvm_surface_begin(s,1));assert(dvm_surface_page(s,page(1477))==DVM_SURFACE_ALIAS);dvm_surface_abort(s);
    /* A stale ID never releases the live overlapping page. */
    assert(dvm_surface_retire(s,first+1)==DVM_SURFACE_STALE);assert(s->owners[1477]);
    assert(!dvm_surface_retire(s,first));assert(!s->active_bytes&&!s->owners[1477]);
    assert(dvm_surface_retire(s,first)==DVM_SURFACE_STALE);
    uint64_t second=add(s,0,2,16383);assert(second>first&&dvm_surface_find(s,second)->count==2);
    assert(dvm_surface_find(s,first)==NULL);assert(!dvm_surface_retire(s,second));
    s->owners[0]=DVM_SURFACE_LEGACY_OWNER;
    s->next_offset=0;assert(!dvm_surface_begin(s,1));assert(dvm_surface_page(s,page(0))==DVM_SURFACE_ALIAS);
    dvm_surface_abort(s);assert(s->owners[0]==DVM_SURFACE_LEGACY_OWNER);s->owners[0]=0;
    for(unsigned i=0;i<4;i++)add(s,i*4096,DVM_SURFACE_MAX_BYTES,0);
    assert(s->active_bytes==DVM_SURFACE_TOTAL_BYTES);assert(dvm_surface_begin(s,1)==DVM_SURFACE_CAPACITY);
    memset(s,0,sizeof(*s));for(unsigned i=0;i<DVM_SURFACE_SLOTS;i++)add(s,i,1,0);
    assert(dvm_surface_begin(s,1)==DVM_SURFACE_CAPACITY);
    memset(s,0,sizeof(*s));s->next_id=UINT32_MAX;assert(dvm_surface_begin(s,1)==DVM_SURFACE_CAPACITY);
    s->poisoned=true;assert(dvm_surface_begin(s,1)==DVM_SURFACE_POISONED);
    free(s);puts("PASS registry: byte/page extents, malformed pages, aliases, legacy exclusion, stale IDs, retirement, budgets, poison");
}
