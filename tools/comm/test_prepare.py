import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare import derive,SERVICE

class EndpointOwnership(unittest.TestCase):
    def source(self):
        return {'LaunchDaemons':{
            '/System/Library/LaunchDaemons/com.apple.CommCenter.plist':{
                'ProgramArguments':['/System/Library/Frameworks/CoreTelephony.framework/Support/CommCenter'],
                'MachServices':{SERVICE:True,'other':True},'KeepAlive':True},
            'unrelated':{'MachServices':{'another':True},'UserName':'mobile'}}}
    def test_moves_one_endpoint_preserving_native_services(self):
        source=self.source();before=copy.deepcopy(source);derive(source)
        jobs=source['LaunchDaemons'];native='/System/Library/LaunchDaemons/com.apple.CommCenter.plist'
        expected=before['LaunchDaemons'][native];del expected['MachServices'][SERVICE]
        self.assertEqual(jobs[native],expected)
        self.assertEqual(jobs['unrelated'],before['LaunchDaemons']['unrelated'])
        owners=[j for j in jobs.values() if SERVICE in j.get('MachServices',{})]
        self.assertEqual(len(owners),1)
        self.assertEqual(owners[0]['ProgramArguments'],['/usr/local/libexec/dvm-cellular-plan'])
    def test_rejects_unknown_owner(self):
        source=self.source();source['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.CommCenter.plist']['ProgramArguments']=['changed']
        with self.assertRaises(ValueError):derive(source)
    def test_rejects_missing_endpoint(self):
        source=self.source();source['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.CommCenter.plist']['MachServices'].pop(SERVICE)
        with self.assertRaises(ValueError):derive(source)
if __name__=='__main__':unittest.main()
