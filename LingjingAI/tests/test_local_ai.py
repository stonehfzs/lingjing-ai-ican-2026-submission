"""Offline v0.6 service/worker boundary checks in disposable workspaces."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server
from local_ai import engine_catalog, require_engine, validate_spec, run_worker
from workflow import write, WorkflowError
from workspaces import WorkspaceRegistry, WorkspacePath, WorkspaceStoreProxy, scope
from editing import LocalJobs

class LocalAITest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        write(self.root/'data/project.json',{'id':'root','title':'test','shots':[]})
        self.registry=WorkspaceRegistry(self.root)
        self.w=self.registry.create({'name':'A','activate':False})
        self.other=self.registry.create({'name':'B','activate':False})
        self.worker=self.root/'worker.py';self.worker.write_text('# isolated stub',encoding='utf8')
        self.manifest={'asr':{'ready':True,'python':sys.executable,'worker':str(self.worker)},'tts':{'ready':True,'python':sys.executable,'worker':str(self.worker)}}
        write(self.root/'data/local-speech-deployment.json',self.manifest)
        for name in ['a.wav','ref.wav']:(self.w.media/name).write_bytes(b'fixture')
        self.rows={'a':{'id':'a','path':str(self.w.media/'a.wav'),'duration':3,'streams':[{'codec_type':'audio'}]},'ref':{'id':'ref','path':str(self.w.media/'ref.wav'),'duration':3,'streams':[{'codec_type':'audio'}]}}
        self.resolve=lambda w,aid,kinds:copy.deepcopy(self.rows[aid]) if w.id==self.w.id and aid in self.rows else self.missing()
    def missing(self):raise WorkflowError('Not in this project')
    def test_missing_worker_or_unverified_manifest_never_ready(self):
        self.manifest['asr']['worker']=str(self.root/'absent.py');self.manifest['tts']['ready']=False
        write(self.root/'data/local-speech-deployment.json',self.manifest)
        self.assertFalse(any(e['ready'] for e in engine_catalog(self.root)))
        with self.assertRaises(WorkflowError):require_engine(self.root,'tts')
    def test_input_must_resolve_in_the_requested_workspace(self):
        with self.assertRaises(WorkflowError):validate_spec(self.other,self.root,{'operation':'transcribe','assetId':'a'},self.resolve)
        value=validate_spec(self.w,self.root,{'operation':'transcribe','assetId':'a','sourcePath':'C:/unrelated.wav'},self.resolve)
        self.assertEqual(value['sourcePath'],self.rows['a']['path'])
    def test_tts_cue_identity_is_derived_from_script_not_client(self):
        doc=json.loads(self.w.project.read_text(encoding='utf8'));doc['shots']=[{'id':'s1','audioPlan':{'cues':[{'cueId':'c1','voiceId':'actor','text':'你好。'}]}}];write(self.w.project,doc)
        spec={'operation':'tts','referenceAssetId':'ref','text':'你好。','shotId':'s1','cueId':'c1','voiceId':'wrong'}
        value=validate_spec(self.w,self.root,spec,self.resolve);self.assertEqual(value['voiceId'],'actor')
        for changes in [{'text':'另一句'},{'cueId':'other'},{'shotId':'other'}]:
            with self.assertRaises(WorkflowError):validate_spec(self.w,self.root,{**spec,**changes},self.resolve)
    def test_tts_long_reference_rejected_before_queue(self):
        self.rows['ref']['duration']=31
        with self.assertRaises(WorkflowError):validate_spec(self.w,self.root,{'operation':'tts','referenceAssetId':'ref','text':'一句'},self.resolve)
    def worker_result(self,result):
        directory=self.w.media/'result';directory.mkdir(exist_ok=True)
        def process(args,**kwargs):
            self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1');self.assertEqual(kwargs['env']['TRANSFORMERS_OFFLINE'],'1')
            write(directory/'worker-result.json',result);return SimpleNamespace(returncode=0)
        with patch('local_ai.subprocess.run',side_effect=process):
            return run_worker(self.w,self.root,{'operation':'transcribe','sourceAssetId':'a','sourceDuration':3},directory,lambda **kw:None)
    def test_transcript_is_candidate_and_real_srt_is_written(self):
        outputs,extra=self.worker_result({'outputs':[],'transcript':{'text':'你好','segments':[{'start':.2,'end':2,'text':'你好'}]}})
        self.assertEqual(outputs,[]);self.assertEqual(extra['transcript']['reviewStatus'],'candidate')
        self.assertIn('00:00:00,200',Path(extra['subtitlePath']).read_text(encoding='utf-8-sig'))
    def test_worker_cannot_register_files_outside_output_folder(self):
        with self.assertRaises(WorkflowError):self.worker_result({'outputs':[{'path':str(self.w.media/'a.wav'),'kind':'audio'}]})
    def test_invalid_transcript_time_rejected(self):
        for end in [float('nan'),float('inf'),-2,500,True]:
            with self.assertRaises(WorkflowError):self.worker_result({'transcript':{'segments':[{'start':0,'end':end,'text':'test'}]}})
    def test_health_and_catalog_do_not_contact_design(self):
        local=LocalJobs(self.root)
        gw=Mock();gw.models.side_effect=AssertionError('cloud forbidden');gw.health.side_effect=AssertionError('cloud forbidden')
        write(self.w.data/'model-catalog.json',{'video':[{'id':'cached'}],'image':[],'speech':[]})
        with patch.multiple(server,ROOT=self.root,REGISTRY=self.registry,WORKFLOW=WorkspaceStoreProxy(self.registry),DATA=WorkspacePath(self.registry,'data'),MEDIA=WorkspacePath(self.registry,'media'),PROJECT=WorkspacePath(self.registry,'project'),JOBS=WorkspacePath(self.registry,'jobs'),LOCAL_JOBS=local,GW=gw),scope(self.w):
            h=object.__new__(server.Handler);h.json_response=lambda value,*args:value
            health=h.get('/api/health');catalog=h.get('/api/models')
            self.assertTrue(health['ok']);self.assertEqual(health['workspaceId'],self.w.id)
            self.assertEqual(catalog['video'][0]['id'],'cached');gw.models.assert_not_called();gw.health.assert_not_called()

    def test_local_tts_attaches_candidate_only_to_unchanged_original_cue(self):
        project=json.loads(self.w.project.read_text(encoding='utf8'))
        project['shots']=[{'id':'s1','bindings':[],'audioPlan':{'cues':[{'cueId':'c1','text':'你好'}]}}]
        write(self.w.project,project)
        job={'id':'j1','operation':'tts','status':'succeeded','payload':{'shotId':'s1','cueId':'c1','text':'你好'},'assets':[{'id':'a1'}]}
        local=Mock();local.get.return_value=[job]
        with patch.multiple(server,REGISTRY=self.registry,PROJECT=WorkspacePath(self.registry,'project'),LOCAL_JOBS=local),patch.object(server,'update_shot') as update:
            with scope(self.other):server.execute_local_job(self.w,'j1')
            self.assertEqual(update.call_args.args[0],'s1')
            body=update.call_args.args[1]
            self.assertEqual(body['bindings'][0]['usage'],'post_mix')
            self.assertEqual(body['audioPlan']['performances'][0]['status'],'candidate')
            update.reset_mock();project['shots'][0]['audioPlan']['cues'][0]['text']='已改';write(self.w.project,project)
            server.execute_local_job(self.w,'j1');update.assert_not_called()
            self.assertIn('attachmentWarning',local.update.call_args.kwargs)

if __name__=='__main__':unittest.main()
