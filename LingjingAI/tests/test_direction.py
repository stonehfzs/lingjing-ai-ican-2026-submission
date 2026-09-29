"""Behavioural checks with an unrelated screenplay; no network/model calls."""
import copy
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from direction import scaffold, validate_plan, save_plan, coverage_report, audition_spec, shot_digest
from audio_service import render_performance
from workflow import WorkflowError
from gateway import GatewayError


class DirectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'station.png';self.file.write_bytes(b'reference-content')
        self.project={'id':'orbital-repair','script':'# S01 空间站\n\n维修员把钥匙放到桌上。\n\n# S02 舱门\n\n空手拉动舱门。',
            'shots':[{'id':'S01','title':'放钥匙','action':'放钥匙','bindings':[{'assetId':'station','usage':'model_reference'}]},
                     {'id':'S02','title':'开门','action':'空手开门','bindings':[{'assetId':'station','usage':'model_reference'}]}]}
        self.assets={'assets':[{'id':'station','path':str(self.file),'mediaType':'image','reviewStatus':'ready','sha256':'v1'}]}
        self.graph={'nodes':[],'edges':[]};self.plan=scaffold(self.project)
        for row in self.plan['shots']:
            row.update(analysisStatus='prepared',requirements=[{'id':'space','label':'空间站舱壁','mediaType':'image','usage':'model_reference',
                'assetIds':['station'],'assetHashes':{'station':'v1'}}])
        self.plan['shots'][0]['continuityOut']={'key':'on_table'}
        self.plan['shots'][1].update(continuityIn={'key':'on_table'},continuityLinks=[{'fromShotId':'S01','keys':['key']}])
    def report(self):return coverage_report(self.plan,self.project,self.graph,self.assets)
    def test_unrelated_screenplay_does_not_inherit_example(self):
        self.assertEqual(scaffold(self.project)['characters'],[])
        self.assertEqual(self.report()['summary']['referenceReady'],2)
    def test_missing_analysis_is_not_ready(self):
        self.assertEqual(coverage_report(None,self.project,self.graph,self.assets)['summary']['referenceReady'],0)
    def test_empty_requirements_not_a_success(self):
        self.plan['shots'][0]['requirements']=[]
        self.assertFalse(self.report()['shots'][0]['referenceReady'])
    def test_disabled_reference(self):
        self.project['shots'][0]['bindings'][0]['enabled']=False
        self.assertFalse(self.report()['shots'][0]['referenceReady'])
    def test_wrong_usage(self):
        self.project['shots'][0]['bindings'][0]['usage']='context'
        self.assertFalse(self.report()['shots'][0]['referenceReady'])
    def test_missing_actual_file(self):
        self.file.unlink()
        self.assertEqual(self.report()['summary']['referenceReady'],0)
    def test_asset_revision_requires_recheck(self):
        self.assets['assets'][0]['sha256']='v2'
        self.assertEqual(self.report()['summary']['referenceReady'],0)
    def test_changed_action_and_prompt_invalidate_analysis(self):
        for field in ['action','videoPrompt']:
            original=copy.deepcopy(self.project)
            self.project['shots'][0][field]='把钥匙留在手里'
            self.assertFalse(self.report()['shots'][0]['referenceReady'])
            self.project=original
    def test_script_edit_invalidates_all(self):
        self.project['script']+='\n新结尾'
        self.assertEqual(self.report()['summary']['referenceReady'],0)
        with self.assertRaises(WorkflowError):validate_plan(self.plan,self.project)
    def test_actual_continuity_conflict(self):
        self.plan['shots'][1]['continuityIn']['key']='in_hand'
        self.assertTrue(any('连续性不符' in s for s in self.report()['shots'][1]['errors']))
    def test_fake_evidence_and_other_project_rejected(self):
        self.plan['shots'][0]['evidenceBlockIds']=['missing-block']
        with self.assertRaises(WorkflowError):validate_plan(self.plan,self.project)
        self.plan=scaffold(self.project);self.plan['projectId']='another-project'
        with self.assertRaises(WorkflowError):validate_plan(self.plan,self.project)
    def test_reuse_cannot_hide_source_gap(self):
        self.plan['shots'][1].update(requirements=[],reuseFromShotId='S01')
        self.project['shots'][0]['bindings']=[]
        self.assertFalse(self.report()['shots'][1]['referenceReady'])
    def test_reuse_cycle_rejected(self):
        self.plan['shots'][0]['reuseFromShotId']='S02';self.plan['shots'][1]['reuseFromShotId']='S01'
        with self.assertRaises(WorkflowError):validate_plan(self.plan,self.project)
    def test_revision_conflict_and_snapshot(self):
        path=Path(self.tmp.name)/'direction.json';saved=save_plan(path,self.plan,self.project)
        with self.assertRaises(WorkflowError):save_plan(path,self.plan,self.project)
        save_plan(path,saved,self.project)
        self.assertTrue((path.parent/'snapshots/direction-r1.json').is_file())
    def test_disabled_node_binding_wins_over_edge(self):
        self.graph={'nodes':[{'id':'a','type':'asset','assetId':'station'},
            {'id':'v','type':'video','shotId':'S01','data':{'bindings':[{'assetId':'station','usage':'model_reference','enabled':False}]}}],
            'edges':[{'source':'a','target':'v','targetPort':'references'}]}
        self.assertFalse(self.report()['shots'][0]['referenceReady'])
    def test_audition_does_not_speak_director_notes(self):
        self.plan['characters']=[{'id':'repair','name':'维修员','voiceDesign':'自然对白','auditionCases':[
            {'id':'line','text':'门锁好了。你走吧。','intention':'担心对方','params':{'speed':1},'performance':[{'after':'门锁好了。','pause':.2}]}]}]
        spec=audition_spec(self.plan,'repair','line','speech','voice-1')
        self.assertEqual(spec['text'],'门锁好了。你走吧。');self.assertNotIn('shotId',spec)
        self.assertEqual(render_performance(spec['text'],spec['performance']),'门锁好了。<#0.2#>你走吧。')
    def test_ambiguous_end_and_arbitrary_acting_marks_rejected(self):
        for text,marks in [('来。来。',[{'after':'来。','pause':.2}]),('走吧。',[{'after':'走吧。','pause':.2}]),
                           ('来。走吧。',[{'after':'来。','tag':'say in fear'}]),('来。走吧。',[{'after':'来。','pause':9}])]:
            with self.assertRaises(GatewayError):render_performance(text,marks)

if __name__=='__main__':unittest.main()
