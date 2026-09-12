"""
Standalone in-house video grading/trim/stabilization worker.

NOT wired into the live post-production pipeline (`app/services/post_production.py`,
`app/routers/post_production.py`) or imported by `app/main.py` / anything Vercel
deploys. See `video_worker/grade.py`'s module docstring and CLAUDE.md's
"Motion (Video) Enhancement -- Researched, Not Deployed" section for why, and
what would need to be true before it is.
"""
