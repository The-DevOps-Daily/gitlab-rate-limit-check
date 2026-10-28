FROM alpine:3.22
# anonymous: every image build fetches this through the builder's IP
ADD https://gitlab.com/acme/tools/-/raw/main/healthcheck.sh /usr/local/bin/healthcheck
RUN chmod +x /usr/local/bin/healthcheck
add https://gitlab.com/acme/tools/-/raw/main/motd /etc/motd
