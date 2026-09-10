FROM nvh-gdpevo-base:v1
COPY gateway.py /app/gateway.py
USER 1000:1000
CMD ["python3", "/app/gateway.py"]
