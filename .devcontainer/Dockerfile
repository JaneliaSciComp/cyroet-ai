FROM node:20-bookworm

ARG TZ=UTC
ENV TZ=$TZ

RUN apt-get update && apt-get install -y --no-install-recommends \
      less \
      git \
      procps \
      sudo \
      fzf \
      zsh \
      man-db \
      unzip \
      gnupg2 \
      ca-certificates \
      iptables \
      ipset \
      iproute2 \
      dnsutils \
      aggregate \
      jq \
      curl \
  && rm -rf /var/lib/apt/lists/*

# Persist bash history across rebuilds via named volume.
RUN mkdir -p /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R node:node /commandhistory

ENV DEVCONTAINER=true

# Allow the default `node` user to run the firewall script (and only that) without a password.
RUN echo 'node ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh' > /etc/sudoers.d/node-firewall \
  && chmod 0440 /etc/sudoers.d/node-firewall

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod 0755 /usr/local/bin/init-firewall.sh \
  && chown root:root /usr/local/bin/init-firewall.sh

USER node

# Global npm dir owned by `node` so Claude Code installs without sudo.
ENV NPM_CONFIG_PREFIX=/home/node/.npm-global
ENV PATH=$PATH:/home/node/.npm-global/bin
RUN mkdir -p /home/node/.npm-global \
  && npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
