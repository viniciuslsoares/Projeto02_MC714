"""Configura o sys.path para que `import src...` funcione a partir da raiz no pytest."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

