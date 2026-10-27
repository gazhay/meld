#!/bin/sh
set -eu

OS=$(uname -s)

# Use sudo only if we're not root and it exists
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO=sudo; fi

# Dump the job environment first, before any package installs change it
export -p > "$HOME/ci-env.sh"
chmod 600 "$HOME/ci-env.sh"

# Linux: make sure sshd, the ssh client and (on Alpine) bash exist
if [ "$OS" = Linux ]; then
  if command -v apk >/dev/null 2>&1; then
    $SUDO apk add --no-cache openssh-server openssh-client bash
  elif [ ! -x /usr/sbin/sshd ] || ! command -v ssh >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
      $SUDO dnf install -y -q openssh-server openssh-clients >/tmp/dnf.log 2>&1 || { cat /tmp/dnf.log; exit 1; }
    elif command -v apt-get >/dev/null 2>&1; then
      $SUDO apt-get update
      $SUDO apt-get install -y openssh-server openssh-client
    else
      echo "No supported package manager, install openssh manually"; exit 1
    fi
  fi
  $SUDO mkdir -p /run/sshd /var/empty/sshd
fi

# Load the job environment in new shells
if [ "$OS" = Linux ]; then
  echo '[ -f ~/.bashrc ] && . ~/.bashrc' >> "$HOME/.bash_profile"
fi
echo ". \"\$HOME/ci-env.sh\"; cd \"$CI_PROJECT_DIR\"" >> "$HOME/.zshrc"
echo ". \"\$HOME/ci-env.sh\"; cd \"$CI_PROJECT_DIR\"" >> "$HOME/.bash_profile"

# If the login shell is not bash (Alpine/ash), switch interactive logins to bash
if [ "$OS" = Linux ]; then
  cat >> "$HOME/.profile" <<'EOF'
if [ -z "${BASH_VERSION:-}" ] && command -v bash >/dev/null 2>&1; then exec bash -l; fi
EOF
fi

# Quality of life for the shell
cat >> "$HOME/.zshrc" <<'EOF'
bindkey '^[[H'  beginning-of-line
bindkey '^[[F'  end-of-line
bindkey '^[OH'  beginning-of-line
bindkey '^[OF'  end-of-line
bindkey '^[[1~' beginning-of-line
bindkey '^[[4~' end-of-line
bindkey '^[[3~' delete-char
bindkey '^[[1;5D' backward-word
bindkey '^[[1;5C' forward-word
bindkey '^[[5D'   backward-word
bindkey '^[[5C'   forward-word
autoload -U up-line-or-beginning-search down-line-or-beginning-search
zle -N up-line-or-beginning-search
zle -N down-line-or-beginning-search
bindkey '^[[A' up-line-or-beginning-search
bindkey '^[[B' down-line-or-beginning-search
bindkey '^[OA' up-line-or-beginning-search
bindkey '^[OB' down-line-or-beginning-search
EOF
cat >> "$HOME/.inputrc" <<'EOF'
"\e[H": beginning-of-line
"\e[F": end-of-line
"\eOH": beginning-of-line
"\eOF": end-of-line
"\e[1~": beginning-of-line
"\e[4~": end-of-line
"\e[3~": delete-char
"\e[1;5D": backward-word
"\e[1;5C": forward-word
"\e[5D": backward-word
"\e[5C": forward-word
"\e[A": history-search-backward
"\e[B": history-search-forward
"\eOA": history-search-backward
"\eOB": history-search-forward
EOF

# ls colors: BSD ls on macOS, GNU/busybox ls on Linux
if [ "$OS" = Darwin ]; then LS_FLAGS=-G; else LS_FLAGS=--color=auto; fi
for rc in .zshrc .bash_profile; do
  cat >> "$HOME/$rc" <<EOF
alias ls='ls $LS_FLAGS'
alias ll='ls -lh $LS_FLAGS'
EOF
done

# Unprivileged-style sshd on 127.0.0.1:2222, key login only
ssh-keygen -q -t ed25519 -N "" -f /tmp/hostkey
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
echo ssh-ed25519 "$MY_PUBKEY" > "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
cat > /tmp/sshd_config <<EOF
Port 2222
ListenAddress 127.0.0.1
HostKey /tmp/hostkey
PidFile /tmp/sshd.pid
AuthorizedKeysFile $HOME/.ssh/authorized_keys
PasswordAuthentication no
PermitRootLogin prohibit-password
UsePAM no
StrictModes no
LogLevel DEBUG1
EOF
/usr/sbin/sshd -f /tmp/sshd_config -E /tmp/sshd.log &
touch /tmp/sshd.log
# Uncomment the next line to get the ssh server logs.
#     This should probably only be done for debugging, as it may show some secrets.
# tail -n +1 -f /tmp/sshd.log &
tail_pid=$!

# Unlock the root account, so SSH to it will work, by setting a random password to it
if [ "$(id -u)" -eq 0 ]; then
  echo "root:$(head -c 24 /dev/urandom | base64)" | chpasswd
fi

# Reverse tunnel to the VPS (no set -x: the key must not reach the log)
printf -- '-----BEGIN OPENSSH PRIVATE KEY-----\n%s\n-----END OPENSSH PRIVATE KEY-----\n' \
  "$TUNNEL_KEY" > "$HOME/.ssh/id_ed25519"
chmod 600 "$HOME/.ssh/id_ed25519"
ssh -N -R localhost:2222:localhost:2222 "tunnel@$TUNNEL_HOST" \
  -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 &
tunnel_pid=$!
sleep 5
if ! kill -0 "$tunnel_pid" 2>/dev/null; then
  echo "Tunnel failed to start (VPS unreachable, wrong key, or port 2222 taken on the VPS)"
  kill "$(cat /tmp/sshd.pid)" 2>/dev/null || true
  exit 1
fi

set +x
echo "Tunnel is up. User is $(whoami). Working directory is $(pwd)"
echo
echo "To connect, run:"
echo "  ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -J $TUNNEL_HOST -p 2222 $(whoami)@localhost"
echo
echo "To end the job, run:"
echo "  touch /tmp/done"

# Keep the job alive for up to 90 minutes, or until /tmp/done exists
i=0
while [ "$i" -lt 5400 ] && [ ! -f /tmp/done ]; do
  sleep 1
  i=$((i + 1))
done

echo "Ending job, killing ssh processes"
kill "$tunnel_pid" 2>/dev/null || true
kill "$(cat /tmp/sshd.pid)" 2>/dev/null || true
kill "$tail_pid" 2>/dev/null || true
