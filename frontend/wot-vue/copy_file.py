
import sys
content = open(sys.argv[1], 'r', encoding='utf-8').read()
open(sys.argv[2], 'w', encoding='utf-8').write(content)
print('Copied')
