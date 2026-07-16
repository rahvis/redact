import os
import sys

# Allow `import fixtures` from any test file regardless of pytest rootdir.
sys.path.insert(0, os.path.dirname(__file__))
