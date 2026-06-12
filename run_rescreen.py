import sys
sys.path.insert(0, '/home/mino/tasi-exec')
from midscreen_ws import run_midscreen

result = run_midscreen(mode='rescreen', picks_file='/home/mino/tasi-exec/picks_1330.json')
if result:
    print(f'Rescreen: {len(result["picks"])} picks')
else:
    print('Rescreen: no picks')