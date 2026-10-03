import subprocess
from pathlib import Path
app=Path(__file__).resolve().parent.parent
launcher=Path.home()/'.local/lib/media-transcribe/boot.sh'
state=Path.home()/'.local/state/media-transcribe'
launcher.parent.mkdir(parents=True,exist_ok=True)
state.mkdir(parents=True,exist_ok=True)
launcher.write_text((app/'scripts/start.sh').read_text())
launcher.chmod(0o700)
line=f'@reboot /bin/bash {launcher} >> {state}/boot.log 2>&1 # chengtin-media-transcribe'
p=subprocess.run(['crontab','-l'],capture_output=True,text=True)
if p.returncode not in (0,1): raise SystemExit(p.stderr)
rows=[s for s in p.stdout.splitlines() if '# chengtin-media-transcribe' not in s]
rows.append(line)
subprocess.run(['crontab','-'],input='\n'.join(rows)+'\n',text=True,check=True)
print('Installed one managed @reboot entry; existing entries preserved.')
