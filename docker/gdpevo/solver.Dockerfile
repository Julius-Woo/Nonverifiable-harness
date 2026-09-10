FROM nvh-gdpevo-base:v1
COPY solver-entrypoint.sh /usr/local/bin/solver-entrypoint
COPY drop-privileges.py /usr/local/bin/drop-privileges.py
RUN chmod 755 /usr/local/bin/solver-entrypoint
RUN mkdir -p /work/input /work/scratch && touch /work/answer.json
ENTRYPOINT ["/usr/local/bin/solver-entrypoint"]
