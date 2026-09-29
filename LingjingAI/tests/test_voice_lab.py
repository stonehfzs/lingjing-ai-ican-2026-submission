"""VoiceLab previews must stay private to their workspace until explicit adoption."""
import copy,json,math,pathlib,struct,shutil,uuid,unittest,wave
from unittest.mock import patch
from workflow import write,WorkflowError
from workspaces import WorkspaceRegistry
from voice_lab import VoiceLab
class VoiceLabTests(unittest.TestCase):
 def setUp(self):
  base=pathlib.Path(__file__).resolve().parent;self.root=base/('.tmp-voice-lab-'+uuid.uuid4().hex);assert self.root.resolve().is_relative_to(base);self.root.mkdir();self.addCleanup(shutil.rmtree,self.root)
  write(self.root/'data/project.json',{'id':'root','title':'test','shots':[]});self.reg=WorkspaceRegistry(self.root);self.a=self.reg.create({'name':'A','activate':False});self.b=self.reg.create({'name':'B','activate':False});self.lab=VoiceLab(self.root)
  self.engines=[{'mode':m,'ready':True,'devices':['cpu'],'deployment':{'modelId':'test-'+m,'modelPath':str(self.root/'model'),'python':'python','revision':'test'}} for m in ['design','reference']]
  self.patcher=patch.object(self.lab,'engines',return_value=self.engines);self.patcher.start();self.addCleanup(self.patcher.stop)
  self.spec={'mode':'design','text':'天快亮了。','description':'年轻自然男声','performance':'平常说话','device':'cpu','requestId':'voice-test-001','seed':1234,'temperature':.9}
 def completed(self,w=None):
  w=w or self.a;t,_=self.lab.prepare(w,self.spec);p=w.media/'voice-lab'/t['id']/'preview.wav';p.parent.mkdir(parents=True)
  with wave.open(str(p),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b''.join(struct.pack('<h',int(4000*math.sin(i*.1))) for i in range(12000)))
  self.lab.update(w,t['id'],status='succeeded',path=str(p),mediaUrl='/media/voice-lab/'+t['id']+'/preview.wav',duration=.5,metadata={'modelId':'test-design'})
  return t['id']
 def test_preview_success_does_not_register_media(self):
  tid=self.completed();self.assertEqual(self.a.store.assets()['assets'],[]);self.assertEqual(self.lab.listing(self.a)['takes'][0]['id'],tid)
  with self.assertRaises(WorkflowError):self.lab.adopt(self.a,{'takeId':tid})
  self.assertEqual(self.a.store.assets()['assets'],[])
 def test_explicit_adoption_is_idempotent_and_preserves_preview(self):
  tid=self.completed();one=self.lab.adopt(self.a,{'takeId':tid,'confirmed':True,'name':'角色试听','role':'voice_identity'});two=self.lab.adopt(self.a,{'takeId':tid,'confirmed':True,'name':'角色试听','role':'voice_identity'})
  self.assertEqual(one['asset']['id'],two['asset']['id']);self.assertEqual(len(self.a.store.assets()['assets']),1);self.assertEqual(one['asset']['role'],'voice_identity');self.assertTrue(pathlib.Path(self.lab.read(self.a)['takes'][0]['path']).exists())
 def test_cross_workspace_take_reference_and_adoption_rejected(self):
  tid=self.completed()
  with self.assertRaises(WorkflowError):self.lab.adopt(self.b,{'takeId':tid,'confirmed':True})
  with self.assertRaises(WorkflowError):self.lab.prepare(self.b,{**self.spec,'mode':'reference','performance':'','referenceTakeId':tid})
  self.assertEqual(self.b.store.assets()['assets'],[])
 def test_reference_preview_reuses_take_without_adoption(self):
  tid=self.completed();t,fresh=self.lab.prepare(self.a,{**self.spec,'requestId':'voice-test-clone','mode':'reference','performance':'','referenceTakeId':tid,'text':'我们回去吧。'})
  stored=next(row for row in self.lab.read(self.a)['takes'] if row['id']==t['id'])
  self.assertTrue(fresh);self.assertEqual(stored['workerRequest']['referenceText'],self.spec['text']);self.assertEqual(self.a.store.assets()['assets'],[])
 def test_idempotency_prevents_changed_payload_retry(self):
  t,_=self.lab.prepare(self.a,self.spec);again,fresh=self.lab.prepare(self.a,self.spec);self.assertFalse(fresh);self.assertEqual(t['id'],again['id'])
  with self.assertRaises(WorkflowError):self.lab.prepare(self.a,{**self.spec,'text':'不同台词'})
 def test_draft_isolation_and_revision_conflict(self):
  self.lab.save_draft(self.a,{'revision':0,'draft':self.spec});self.assertEqual(self.lab.read(self.b)['draft'],{})
  with self.assertRaises(WorkflowError):self.lab.save_draft(self.a,{'revision':0,'draft':self.spec})
 def test_cancelled_queue_never_starts_worker(self):
  t,_=self.lab.prepare(self.a,self.spec);self.lab.cancel(self.a,{'takeId':t['id']})
  with patch('voice_lab.subprocess.Popen') as popen:self.lab.execute(self.a,t['id']);popen.assert_not_called()
  self.assertEqual(self.lab.read(self.a)['takes'][0]['status'],'cancelled')
 def test_reference_mode_rejects_ignored_performance_instructions(self):
  tid=self.completed()
  with self.assertRaises(WorkflowError):self.lab.prepare(self.a,{**self.spec,'requestId':'bad-reference','mode':'reference','referenceTakeId':tid})
 def test_unready_engine_and_invalid_numbers_rejected(self):
  self.engines[0]['ready']=False
  with self.assertRaises(WorkflowError):self.lab.prepare(self.a,self.spec)
  for change in [{'seed':True},{'seed':1.4},{'temperature':float('nan')},{'mode':'cloud'}]:
   with self.assertRaises(WorkflowError):self.lab.clean_draft({**self.spec,**change})

 def test_personal_library_adoption_keeps_project_library_empty(self):
  from personal_library import PersonalLibrary
  tid=self.completed();library=PersonalLibrary(self.root)
  saved=self.lab.adopt(self.a,{'takeId':tid,'confirmed':True,'target':'library','role':'voice_identity'},library)
  self.assertEqual(saved['target'],'library');self.assertEqual(self.a.store.assets()['assets'],[]);self.assertEqual(len(library.listing()['assets']),1)
  origin=library.listing()['assets'][0]['provenance']['origin'];self.assertEqual(origin['spec']['seed'],1234)
 def test_queue_limit_rejects_additional_gpu_work(self):
  for i in range(3):self.lab.prepare(self.a,{**self.spec,'requestId':'queue-test-'+str(i)})
  with self.assertRaises(WorkflowError):self.lab.prepare(self.a,{**self.spec,'requestId':'queue-test-4'})
