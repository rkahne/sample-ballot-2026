"""
Gunicorn configuration for production deployment on Digital Ocean.
Usage: gunicorn -c gunicorn.conf.py "app:app"
"""
import multiprocessing

bind             = "0.0.0.0:5001"
workers          = multiprocessing.cpu_count() * 2 + 1
max_requests     = 1000
max_requests_jitter = 100
timeout          = 30
graceful_timeout = 10
keepalive        = 2
loglevel         = "info"
accesslog        = "-"
errorlog         = "-"
proc_name        = "sample-ballot-2026"
worker_tmp_dir   = "/tmp"
