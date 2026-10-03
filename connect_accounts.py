"""Open the official subscription login using an installed account client."""
import subprocess
import sys
def main():
    provider=sys.argv[1]
    if provider=='grok':
        from grok_provider import EXE
        if not EXE.is_file():raise SystemExit('Install official Grok Build first: https://docs.x.ai/build/overview')
        command=[str(EXE),'login','--oauth']
    elif provider=='claude':
        from claude_provider import executable
        exe=executable()
        if not exe:raise SystemExit('Install official Claude Code first: https://code.claude.com/docs/en/setup')
        command=[exe,'auth','login']
    else:raise SystemExit('Choose grok or claude.')
    raise SystemExit(subprocess.call(command))
if __name__=='__main__':main()
