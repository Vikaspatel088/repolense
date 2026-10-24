"""Safe GitHub public repository clone manager for RepoLens."""
import os
import re
import shutil
import stat
import subprocess
import tempfile

GITHUB_REGEX = re.compile(
    r'^(?:https?://)?(?:www\.)?github\.com/([a-zA-Z0-9_\-\.]+)/([a-zA-Z0-9_\-\.]+?)(?:\.git)?/?$',
    re.IGNORECASE
)

MAX_CLONE_TIMEOUT = 30  # seconds


class GitHubCloneError(Exception):
    """Raised when GitHub URL validation or cloning fails."""
    pass


def _on_rm_error(func, path, exc_info):
    """Error handler for shutil.rmtree on Windows read-only files."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def parse_github_url(url: str):
    """Validate and normalize a public GitHub repository URL.
    Returns (owner, repo, normalized_https_url).
    Raises GitHubCloneError on invalid URLs.
    """
    if not isinstance(url, str):
        raise GitHubCloneError("Repository URL must be a string.")
    
    cleaned = url.strip()
    match = GITHUB_REGEX.match(cleaned)
    if not match:
        raise GitHubCloneError(
            "Invalid GitHub URL. Must be in the format 'https://github.com/owner/repository'."
        )
    
    owner, repo = match.group(1), match.group(2)
    # Block dangerous traversal or malicious parameter injection in owner/repo
    for part in (owner, repo):
        if not part or part in ('.', '..') or part.startswith('-'):
            raise GitHubCloneError(f"Invalid repository identifier: '{part}'")
            
    norm_url = f"https://github.com/{owner}/{repo}.git"
    return owner, repo, norm_url


def clone_github_repo(url: str, base_temp_dir: str = None) -> tuple[str, str, str]:
    """Safely clone a public GitHub repository into an isolated temporary directory.
    Uses shallow clone (--depth 1) and enforces timeouts.
    Returns (target_dir, f"{owner}/{repo}", clone_url).
    Raises GitHubCloneError on failure.
    """
    owner, repo, clone_url = parse_github_url(url)
    
    temp_dir = tempfile.mkdtemp(prefix=f"repolens_gh_{owner}_{repo}_", dir=base_temp_dir)
    target_dir = os.path.join(temp_dir, repo)
    
    cmd = [
        'git',
        'clone',
        '--depth', '1',
        '--config', 'core.symlinks=false',
        '--',
        clone_url,
        target_dir
    ]
    
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MAX_CLONE_TIMEOUT,
            shell=False  # Crucial for security: prevent shell injection
        )
        if res.returncode != 0:
            shutil.rmtree(temp_dir, ignore_errors=True, onerror=_on_rm_error)
            err = (res.stderr or '').strip()
            if 'Repository not found' in err or 'Authentication failed' in err or 'could not read Username' in err:
                raise GitHubCloneError(
                    f"GitHub repository '{owner}/{repo}' was not found or is private. "
                    "RepoLens only supports public GitHub repositories."
                )
            raise GitHubCloneError(f"Git clone failed: {err or 'unknown error'}")
            
    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True, onerror=_on_rm_error)
        raise GitHubCloneError(
            f"Cloning '{owner}/{repo}' timed out after {MAX_CLONE_TIMEOUT} seconds. "
            "The repository may be too large or network connectivity is limited."
        )
    except FileNotFoundError:
        shutil.rmtree(temp_dir, ignore_errors=True, onerror=_on_rm_error)
        raise GitHubCloneError("Git executable is not installed or not available in PATH.")
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True, onerror=_on_rm_error)
        if isinstance(e, GitHubCloneError):
            raise
        raise GitHubCloneError(f"Failed to clone GitHub repository: {str(e)}")

    return target_dir, f"{owner}/{repo}", clone_url


def cleanup_clone(path: str):
    """Safely remove a temporary cloned repository if it resides within a temp directory."""
    if not path or not os.path.exists(path):
        return
    real = os.path.realpath(path)
    temp_root = os.path.realpath(tempfile.gettempdir())
    # Ensure we only remove paths inside system temp dir and matching repolens_gh_
    if real.startswith(temp_root) and 'repolens_gh_' in real:
        parent = os.path.dirname(real)
        target = parent if 'repolens_gh_' in os.path.basename(parent) else real
        try:
            shutil.rmtree(target, ignore_errors=False, onerror=_on_rm_error)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
