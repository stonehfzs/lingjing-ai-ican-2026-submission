"""Run the upstream CLI with socket connections disabled for offline inference."""
import runpy,socket
def blocked(*args,**kwargs):
    raise RuntimeError('MuseTalk离线推理禁止网络连接；请先完成本地模型安装。')
socket.socket.connect=blocked
socket.socket.connect_ex=blocked
socket.create_connection=blocked
runpy.run_module('scripts.inference',run_name='__main__')
