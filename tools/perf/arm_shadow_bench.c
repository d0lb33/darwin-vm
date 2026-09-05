/* Measure real guest-EL2 UNDEF emulation, VMM exits and preserving code thunks.
 * Synthetic register model only: does not implement GXF/SPRR/SPTM or boot iOS.
 * full-state adds a GPR+SIMD round trip to each VMM exit as a handoff floor.
 */
#include <Hypervisor/Hypervisor.h>
#include <mach/mach_time.h>
#include <libkern/OSCacheControl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BASE UINT64_C(0x40000000)
#define SIZE 0x40000
#define CHECK(call) do { hv_return_t r=(call); if(r!=HV_SUCCESS) {fprintf(stderr,"%s: 0x%x\n",#call,r); exit(2);} } while(0)
static double now(void) { mach_timebase_info_data_t tb; mach_timebase_info(&tb); return mach_absolute_time()*(double)tb.numer/tb.denom/1e9; }
static uint64_t readreg(hv_vcpu_t cpu, hv_reg_t reg) {uint64_t v; CHECK(hv_vcpu_get_reg(cpu,reg,&v));return v;}
static void roundtrip(hv_vcpu_t cpu) {
    uint64_t gp[33]; hv_simd_fp_uchar16_t fp[32];
    for(int i=0;i<31;i++) CHECK(hv_vcpu_get_reg(cpu,HV_REG_X0+i,&gp[i]));
    CHECK(hv_vcpu_get_reg(cpu,HV_REG_PC,&gp[31]));
    CHECK(hv_vcpu_get_reg(cpu,HV_REG_CPSR,&gp[32]));
    for(int i=0;i<32;i++) CHECK(hv_vcpu_get_simd_fp_reg(cpu,HV_SIMD_FP_REG_Q0+i,&fp[i]));
    for(int i=0;i<31;i++) CHECK(hv_vcpu_set_reg(cpu,HV_REG_X0+i,gp[i]));
    CHECK(hv_vcpu_set_reg(cpu,HV_REG_PC,gp[31]));
    CHECK(hv_vcpu_set_reg(cpu,HV_REG_CPSR,gp[32]));
    for(int i=0;i<32;i++) CHECK(hv_vcpu_set_simd_fp_reg(cpu,HV_SIMD_FP_REG_Q0+i,fp[i]));
}
int main(int argc,char **argv) {
    if(argc!=5){fprintf(stderr,"usage: arm_shadow_bench raw-payload mode iterations full-state\n");return 2;}
    int mode=atoi(argv[2]), full=atoi(argv[4]); uint64_t n=strtoull(argv[3],NULL,0);
    if(mode<0||mode>3||n==0||n>10000000||(full!=0&&full!=1))return 2;
    bool supported=false; CHECK(hv_vm_config_get_el2_supported(&supported)); if(!supported)return 3;
    alarm(15); /* Process-local hard limit, including unimplemented guest paths. */
    hv_vm_config_t config=hv_vm_config_create(); CHECK(hv_vm_config_set_el2_enabled(config,mode!=3)); CHECK(hv_vm_create(config));
    uint8_t *mem=mmap(NULL,SIZE,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0); if(mem==MAP_FAILED)return 2;
    FILE *f=fopen(argv[1],"rb");if(!f)return 2;size_t len=fread(mem,1,SIZE,f);if(ferror(f)||!feof(f)||len<0x20010)return 2;fclose(f);
    CHECK(hv_vm_map(mem,BASE,SIZE,HV_MEMORY_READ|HV_MEMORY_WRITE|HV_MEMORY_EXEC));
    hv_vcpu_t cpu;hv_vcpu_exit_t *ex;CHECK(hv_vcpu_create(&cpu,&ex,NULL));
    CHECK(hv_vcpu_set_sys_reg(cpu,mode==3?HV_SYS_REG_SCTLR_EL1:HV_SYS_REG_SCTLR_EL2,mode==3?0x30d00800:0x30c50830));
    if(mode!=3)CHECK(hv_vcpu_set_sys_reg(cpu,HV_SYS_REG_HCR_EL2,UINT64_C(1)<<31));
    CHECK(hv_vcpu_set_sys_reg(cpu,mode==3?HV_SYS_REG_VBAR_EL1:HV_SYS_REG_VBAR_EL2,BASE+0x1000));
    CHECK(hv_vcpu_set_sys_reg(cpu,mode==3?HV_SYS_REG_SP_EL1:HV_SYS_REG_SP_EL2,BASE+SIZE-16));
    if(mode!=3)CHECK(hv_vcpu_set_sys_reg(cpu,HV_SYS_REG_CPTR_EL2,0x33ff));
    CHECK(hv_vcpu_set_reg(cpu,HV_REG_PC,BASE));
    CHECK(hv_vcpu_set_reg(cpu,HV_REG_CPSR,mode==3?0x3c5:0x3c9));
    CHECK(hv_vcpu_set_reg(cpu,HV_REG_X0,n));
    for(int i=1;i<31;i++)CHECK(hv_vcpu_set_reg(cpu,HV_REG_X0+i,0xface0000+i));
    sys_icache_invalidate(mem,SIZE);
    uint64_t exits=0, *shadow=(uint64_t *)(mem+0x20000); double start=now();
    for(;;){
        CHECK(hv_vcpu_run(cpu));
        if(ex->reason==HV_EXIT_REASON_VTIMER_ACTIVATED){CHECK(hv_vcpu_set_vtimer_mask(cpu,true));continue;}
        uint64_t esr=ex->exception.syndrome;
        if(ex->reason!=HV_EXIT_REASON_EXCEPTION || (esr>>26)!=0x17){fprintf(stderr,"unexpected exit %d ESR=%"PRIx64" PC=%"PRIx64"\n",ex->reason,esr,readreg(cpu,HV_REG_PC));return 4;}
        unsigned imm=esr&0xffff;
        if(imm==0x7f)break;
        if(mode!=1||(imm!=0x10&&imm!=0x11)){fprintf(stderr,"unexpected SMC %x, guest handler validation failed\n",imm);return 4;}
        if(full)roundtrip(cpu);
        if(imm==0x10)CHECK(hv_vcpu_set_reg(cpu,HV_REG_X9,*shadow));
        else *shadow=readreg(cpu,HV_REG_X10);
        /* SMC exits leave PC on the instruction in this API. Verify whether
         * it still names a matching SMC before advancing, instead of assuming. */
        uint64_t pc=readreg(cpu,HV_REG_PC);
        if(pc>=BASE&&pc+4<=BASE+len){uint32_t insn;memcpy(&insn,mem+pc-BASE,4);if(insn==(0xd4000003u|(imm<<5)))CHECK(hv_vcpu_set_reg(cpu,HV_REG_PC,pc+4));}
        exits++;
    }
    double seconds=now()-start;uint64_t expected=0;
    for(uint64_t i=0;i<n;i++){expected^=0x1234+i;expected=(expected>>7)|(expected<<57);}
    uint64_t checksum=readreg(cpu,HV_REG_X11);
    if(shadow[0]!=0x1234+n||checksum!=expected||readreg(cpu,HV_REG_X0)!=0||
       shadow[1]!=((mode==0||mode==3)?2*n:0)||exits!=(mode==1?2*n:0)){fprintf(stderr,"verification failed: shadow=%"PRIx64" count=%"PRIu64" exits=%"PRIu64" checksum=%"PRIx64" expected=%"PRIx64"\n",shadow[0],shadow[1],exits,checksum,expected);return 5;}
    for(int i=1;i<31;i++)if((i<9||i>11)&&readreg(cpu,HV_REG_X0+i)!=(uint64_t)(0xface0000+i)){fprintf(stderr,"clobbered x%d\n",i);return 5;}
    printf("{\"mode\":%d,\"full_state\":%d,\"iterations\":%"PRIu64",\"seconds\":%.9f,\"ns_per_access\":%.3f,\"vmm_exits\":%"PRIu64",\"guest_faults\":%"PRIu64",\"checksum\":\"%"PRIx64"\",\"verified\":true}\n",mode,full,n,seconds,seconds/(2*n)*1e9,exits,shadow[1],checksum);
    CHECK(hv_vcpu_destroy(cpu));CHECK(hv_vm_unmap(BASE,SIZE));CHECK(hv_vm_destroy());munmap(mem,SIZE);alarm(0);return 0;
}
