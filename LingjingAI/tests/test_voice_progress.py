"""Progress uses real worker phases and cannot revive a cancelled/recovered take."""
import json
import os
import pathlib
import shutil
import subprocess
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from voice_lab import VoiceLab
from workflow import write
with patch.dict(os.environ):
 from tools.voice_lab_worker import report_progress


class VoiceProgressTests(unittest.TestCase):
 def setUp(self):
  base=pathlib.Path(__file__).resolve().parent
  self.root=base/('.tmp-voice-progress-'+uuid.uuid4().hex)
  self.root.mkdir();self.addCleanup(shutil.rmtree,self.root)
  self.w=SimpleNamespace(id='isolated-progress',data=self.root/'data',media=self.root/'media')
  self.lab=VoiceLab(self.root)
  self.engines=[{'mode':'design','ready':True,'devices':['cpu'],'deployment':{'modelId':'test','modelPath':'unused','python':'unused'}}]
  self.patcher=patch.object(self.lab,'engines',return_value=self.engines)
  self.patcher.start();self.addCleanup(self.patcher.stop)
  self.take,_=self.lab.prepare(self.w,{'requestId':'progress-test-001','mode':'design','device':'cpu','description':'测试声线','text':'测试。'})
  self.tid=self.take['id'];self.directory=self.w.media/'voice-lab'/self.tid
  self.progress=self.directory/'progress.json'

 def current(self):return self.lab.read(self.w)['takes'][0]

 def start(self):
  self.lab.update(self.w,self.tid,status='running',progressRunId='current-run',startedAt='test-start')

 def emit(self,phase,run_id='current-run'):
  write(self.progress,{'runId':run_id,'phase':phase,'percent':90,'indeterminate':False})
  self.lab.collect_progress(self.w,self.tid,self.progress,'current-run')

 def test_queued_and_real_phases_are_indeterminate_without_invented_percent(self):
  self.assertEqual(self.take['progress'],{'phase':'queued','percent':None,'indeterminate':True})
  self.assertIsNone(self.take['startedAt']);self.start()
  for phase in ('loading','generating','encoding'):
   self.emit(phase)
   self.assertEqual(self.current()['progress'],{'phase':phase,'percent':None,'indeterminate':True})
  self.emit('loading')
  self.assertEqual(self.current()['progress']['phase'],'encoding')
  self.assertNotIn('progressRunId',self.lab.public(self.current()))

 def test_corrupt_missing_and_stale_progress_are_ignored(self):
  self.start();self.lab.collect_progress(self.w,self.tid,self.progress,'current-run')
  self.progress.parent.mkdir(parents=True)
  for content in ('{partial', '[]', 'null', '\ufffd', '{"phase":"generating"}'):
   self.progress.write_text(content,encoding='utf-8')
   self.lab.collect_progress(self.w,self.tid,self.progress,'current-run')
   self.assertEqual(self.current()['progress']['phase'],'queued')
  self.emit('encoding','old-run');self.emit('complete')
  self.assertEqual(self.current()['progress']['phase'],'queued')

 def test_cancelled_take_rejects_late_progress_and_never_starts(self):
  self.start();self.emit('loading')
  self.lab.cancel(self.w,{'takeId':self.tid});self.emit('encoding')
  self.assertEqual(self.current()['progress'],{'phase':'cancelled','percent':None,'indeterminate':False})
  with patch('voice_lab.subprocess.Popen') as popen:
   self.lab.execute(self.w,self.tid);popen.assert_not_called()

 def test_restart_converges_pending_progress_and_rejects_old_worker(self):
  self.start();self.emit('generating');self.lab.recover_interrupted(self.w)
  self.emit('encoding')
  self.assertEqual(self.current()['status'],'failed')
  self.assertEqual(self.current()['progress'],{'phase':'failed','percent':None,'indeterminate':False})
  self.assertEqual(self.current()['stage'],'生成失败')

 def test_worker_progress_replaces_file_atomically(self):
  self.directory.mkdir(parents=True)
  self.progress.write_text('old',encoding='utf-8')
  import os
  replace=os.replace
  def check_replace(source,target):
   self.assertEqual(self.progress.read_text(encoding='utf-8'),'old')
   self.assertEqual(json.loads(pathlib.Path(source).read_text())['phase'],'loading')
   replace(source,target)
  with patch('tools.voice_lab_worker.os.replace',side_effect=check_replace):
   report_progress({'outputDir':str(self.directory),'progressRunId':'current-run'},'loading')
  self.assertEqual(json.loads(self.progress.read_text())['runId'],'current-run')
  self.assertEqual(list(self.directory.glob('*.tmp')),[])

 def test_execute_persists_observed_phases_and_completes_only_after_probe(self):
  process=Mock(returncode=0);observed=[]
  def waiting(timeout):
   request=json.loads((self.directory/'request.json').read_text())
   observed.append(self.current()['progress']['phase'])
   phase=('loading','generating','encoding')[min(len(observed)-1,2)]
   report_progress(request,phase)
   if len(observed)<3:raise subprocess.TimeoutExpired('mock',timeout)
   output=self.directory/'preview.wav';output.write_bytes(b'isolated fake audio')
   write(self.directory/'result.json',{'path':str(output),'metadata':{'modelId':'test'}})
  process.wait.side_effect=waiting
  def checked_probe(path,unused):
   self.assertEqual(self.current()['progress']['phase'],'encoding')
   self.assertEqual(self.current()['status'],'running')
   return {'probeStatus':'verified','duration':1}
  with patch('voice_lab.subprocess.Popen',return_value=process),patch('voice_lab.probe',side_effect=checked_probe):
   self.lab.execute(self.w,self.tid)
  self.assertEqual(observed,['queued','loading','generating'])
  self.assertEqual(self.current()['status'],'succeeded')
  self.assertEqual(self.current()['progress'],{'phase':'complete','percent':100,'indeterminate':False})
  self.assertTrue(self.current()['startedAt']);self.assertEqual(self.lab.processes,{})

 def test_cancel_during_worker_failure_stays_cancelled(self):
  process=Mock(returncode=1);process.poll.return_value=None
  def waiting(timeout):
   self.lab.cancel(self.w,{'takeId':self.tid})
   request=json.loads((self.directory/'request.json').read_text())
   report_progress(request,'encoding')
   raise RuntimeError('worker failed after cancellation')
  process.wait.side_effect=waiting
  with patch('voice_lab.subprocess.Popen',return_value=process):self.lab.execute(self.w,self.tid)
  self.assertEqual(self.current()['status'],'cancelled')
  self.assertEqual(self.current()['progress']['phase'],'cancelled')
  self.assertEqual(self.lab.processes,{});process.terminate.assert_called_once()

 def test_failed_audio_verification_never_reports_complete(self):
  process=Mock(returncode=0)
  def waiting(timeout):
   output=self.directory/'preview.wav';output.write_bytes(b'bad audio')
   write(self.directory/'result.json',{'path':str(output),'metadata':{'modelId':'test'}})
  process.wait.side_effect=waiting
  with patch('voice_lab.subprocess.Popen',return_value=process),patch('voice_lab.probe',return_value={'probeStatus':'failed'}):
   self.lab.execute(self.w,self.tid)
  self.assertEqual(self.current()['status'],'failed')
  self.assertEqual(self.current()['progress'],{'phase':'failed','percent':None,'indeterminate':False})

 def test_worker_failure_converges_progress(self):
  process=Mock(returncode=1)
  with patch('voice_lab.subprocess.Popen',return_value=process):self.lab.execute(self.w,self.tid)
  self.assertEqual(self.current()['status'],'failed')
  self.assertEqual(self.current()['progress'],{'phase':'failed','percent':None,'indeterminate':False})

 def test_worker_timeout_stops_process_and_converges_progress(self):
  process=Mock(returncode=1)
  with patch('voice_lab.subprocess.Popen',return_value=process),patch('voice_lab.time.monotonic',side_effect=[0,1801]):
   self.lab.execute(self.w,self.tid)
  process.kill.assert_called_once()
  self.assertEqual(self.current()['status'],'failed')
  self.assertIn('超时',self.current()['error'])
  self.assertEqual(self.current()['progress']['phase'],'failed')
  self.assertEqual(self.lab.processes,{})


if __name__=='__main__':unittest.main()
