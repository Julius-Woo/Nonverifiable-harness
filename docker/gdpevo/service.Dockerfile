FROM nvh-gdpevo-base:v1
RUN pip install --no-cache-dir Flask==3.1.2 Werkzeug==3.1.3 \
    Jinja2==3.1.6 MarkupSafe==3.0.3 click==8.3.1 itsdangerous==2.2.0 blinker==1.9.0
WORKDIR /app
COPY env/ /app/
ENV TASK_ENV_BIND=0.0.0.0 TASK_ENV_PORT=8080 PORT=8080
USER 1000:1000
CMD ["python3", "app.py"]
