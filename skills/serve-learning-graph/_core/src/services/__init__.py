"""
File: src/services/__init__.py

Purpose:
    Non-interactive workflow service layer for the GUI workflow host.

Responsibilities:
    - Host stateless, JSON-able decompositions of the interactive CLI agents
      (assessor / teacher / examiner / reviewer) so the same business logic can
      run behind /api/* HTTP endpoints without a server-side REPL.

What this file does NOT do:
    - Start an HTTP server or read terminal input
    - Render Rich/console output
    - Add DB schema changes

This is the first feature module (goal assess); learn/exam/review services will
join here following the same pure-function, explicit-id conventions.
"""
