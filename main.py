"""
Entry point for the Learning Graph Engine CLI.

Usage:
    python main.py --help
    python main.py init
    python main.py init --help
    python main.py goal --help
    python main.py goal new "学会 Kubernetes 集群管理" --domains "Linux,Docker"
    python main.py goal new --help
    python main.py goal list
    python main.py goal list --help
    python main.py goal remove <goal-id>
    python main.py goal remove --help
    python main.py goal export <goal-id>
    python main.py goal export --help
    python main.py goal tree <goal-id>
    python main.py goal tree --help
    python main.py goal nodes <goal-id>
    python main.py goal nodes --help
    python main.py status
    python main.py status --help
"""

from src.cli.entrypoints import run_main

if __name__ == "__main__":
    run_main()
