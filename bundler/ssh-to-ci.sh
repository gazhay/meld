#!/bin/bash

set -x
set -euo pipefail

# Dump the job environment (before anything else changes it)
export -p > "$HOME/ci-env.sh"
chmod 600 "$HOME/ci-env.sh"
echo "source \"\$HOME/ci-env.sh\"; cd \"$CI_PROJECT_DIR\"" >> "$HOME/.zshrc"
echo "source \"\$HOME/ci-env.sh\"; cd \"$CI_PROJECT_DIR\"" >> "$HOME/.bash_profile"

# Quality of life for the shell
cat >> "$HOME/.zshrc" <<'EOF'
bindkey '^[[H'  beginning-of-line
bindkey '^[[F'  end-of-line
bindkey '^[OH'  beginning-of-line
bindkey '^[OF'  end-of-line
bindkey '^[[1~' beginning-of-line
bindkey '^[[4~' end-of-line
bindkey '^[[3~' delete-char
autoload -U up-line-or-beginning-search down-line-or-beginning-search
zle -N up-line-or-beginning-search
zle -N down-line-or-beginning-search
bindkey '^[[A' up-line-or-beginning-search
bindkey '^[[B' down-line-or-beginning-search
bindkey '^[OA' up-line-or-beginning-search
bindkey '^[OB' down-line-or-beginning-search

alias ls='ls --color'
alias ll='ls --color -lh'
EOF
cat >> "$HOME/.inputrc" <<'EOF'
"\e[H": beginning-of-line
"\e[F": end-of-line
"\eOH": beginning-of-line
"\eOF": end-of-line
"\e[1~": beginning-of-line
"\e[4~": end-of-line
"\e[3~": delete-char
"\e[A": history-search-backward
"\e[B": history-search-forward
"\eOA": history-search-backward
"\eOB": history-search-forward

alias ls='ls --color'
alias ll='ls --color -lh'
EOF

# Unprivileged sshd on 127.0.0.1:2222, key login only
ssh-keygen -q -t ed25519 -N "" -f /tmp/hostkey
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
echo ssh-ed25519 "$MY_PUBKEY" > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && cat ~/.ssh/authorized_keys
cat > /tmp/sshd_config <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey /tmp/hostkey
PidFile /tmp/sshd.pid
AuthorizedKeysFile $HOME/.ssh/authorized_keys
PasswordAuthentication no
UsePAM no
StrictModes no
EOF
/usr/sbin/sshd -f /tmp/sshd_config

# Reverse tunnel to the VPS
printf -- "-----BEGIN OPENSSH PRIVATE KEY-----\n$TUNNEL_KEY\n-----END OPENSSH PRIVATE KEY-----\n" > ~/.ssh/id_ed25519 && chmod 600 ~/.ssh/id_ed25519
ssh -N -R localhost:2222:localhost:2222 tunnel@$TUNNEL_HOST -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 &
tunnel_pid=$!
set +x
echo "Tunnel is up. User is $(whoami). pwd is $(pwd)"
echo
echo "To connect, run:"
echo "  ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -J $TUNNEL_HOST -p 2222 $(whoami)@localhost"
echo
echo "To end the job, run:"
echo "  touch /tmp/done"

# Keep the job alive for up to 90 minutes, or until /tmp/done exists
for i in $(seq 1 5400); do
[ -f /tmp/done ] && break
sleep 1
done

echo "Found /tmp/done, killing ssh processes"
kill "$tunnel_pid" 2>/dev/null || true
kill "$(cat /tmp/sshd.pid)" 2>/dev/null || true
