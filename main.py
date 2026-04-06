"""
Entry point for the Learning Graph Engine CLI.

Usage:
    python main.py init
    python main.py goal new "学会 Kubernetes 集群管理" --domains "Linux,Docker"
    python main.py goal list
    python main.py goal tree <goal-id>
    python main.py goal nodes <goal-id>
    python main.py status
"""

from src.cli.main import app

if __name__ == "__main__":
    app()
