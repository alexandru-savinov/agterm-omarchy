# Creation-only shell startup: never send the capture to an existing daemon's tty.
'builtin' 'typeset' '+x' AGTERM_LIVE_PREFILL AGTERM_LIVE_INTEGRATION
'builtin' 'source' '--' "$AGTERM_LIVE_INTEGRATION/.zshenv"
'builtin' 'print' '-z' '--' "$AGTERM_LIVE_PREFILL"
'builtin' 'unset' AGTERM_LIVE_PREFILL AGTERM_LIVE_INTEGRATION
