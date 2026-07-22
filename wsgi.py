import os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('PRODUCTION', 'true')

from meta_ads_agent import main
main()
