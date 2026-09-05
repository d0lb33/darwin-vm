"""Exercise the compiled header-only guest path against an owned mock client."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from aux_probe import AuxProbe


class HeaderTests(unittest.TestCase):
    def test_one_read_no_writes_and_foreign_magic_rejected(self):
        with tempfile.TemporaryDirectory(dir='/tmp/dvm') as temp:
            out=Path(temp);peer=AuxProbe(out)
            try:
                source=out/'client.c'
                header=Path(__file__).with_name('aux_transport_probe.h').resolve()
                source.write_text('''#include <IOKit/IOKitLib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#define DVM_AUX_UC_TRANSFER
#define DVM_AUX_HEADER_DIAG
#include "'''+str(header)+'''"
static int backend,reads;
static kern_return_t mock(io_connect_t c,uint32_t sel,const uint64_t *in,uint32_t ni,uint64_t *out,uint32_t *no) {
 (void)c;
 if(sel==2||sel==3){if(ni||!out||!no||*no!=1)return 1;*out=sel==2?4096:16384;return 0;}
 if(sel!=0||ni!=3||in[1]!=4096||in[2]!=0)return 2;
 reads++;return pread(backend,(void *)(uintptr_t)in[0],4096,0)==4096?0:3;
}
int main(int argc,char **argv){
 if(argc!=2)return 9;backend=open(argv[1],O_RDONLY);aux_scalar=mock;
 int rc=aux_probe_client(1);close(backend);if(reads!=1)return 10;return rc;
}
''')
                subprocess.run(['xcrun','clang','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',str(source),'-o',str(out/'client')],check=True)
                good=subprocess.run([str(out/'client'),str(peer.path)],capture_output=True,text=True,timeout=5)
                self.assertEqual(good.returncode,0,good.stderr)
                for stage in (1,2,3,4):self.assertIn(f'GPU_LOAD_AUX_STAGE stage={stage} ',good.stderr)
                self.assertIn('GPU_LOAD_AUX_HEADER_ONLY pass=1 bytes=4096',good.stderr)
                self.assertNotIn('GPU_LOAD_AUX_READ bytes=',good.stderr)
                self.assertEqual(os.pread(peer.fd,1048576,0x400000),bytes(1048576))
                os.pwrite(peer.fd,b'foreign',0)
                bad=subprocess.run([str(out/'client'),str(peer.path)],capture_output=True,text=True,timeout=5)
                self.assertEqual(bad.returncode,1,bad.stderr)
                self.assertIn('GPU_LOAD_AUX_HEADER_ONLY pass=0',bad.stderr)
            finally:peer.close()


if __name__=='__main__':unittest.main()
