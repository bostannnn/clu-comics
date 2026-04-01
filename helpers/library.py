import os
import tempfile
from core.app_logging import app_logger


def normalize_real_path(path):
    """Normalize a path and resolve symlinks for any existing parent segments."""
    if not path:
        return ""
    return os.path.normpath(os.path.realpath(path))


def path_is_within_root(path, root):
    """Return True when path is the root itself or a descendant of it."""
    if not path or not root:
        return False

    normalized = normalize_real_path(path)
    root_normalized = normalize_real_path(root)
    return normalized == root_normalized or normalized.startswith(root_normalized + os.sep)


def path_is_parent_of(path, child):
    """Return True when path is the same as child or an ancestor directory of it."""
    if not path or not child:
        return False

    normalized = normalize_real_path(path)
    child_normalized = normalize_real_path(child)
    return child_normalized == normalized or child_normalized.startswith(normalized + os.sep)


def is_path_in_any_root(path, roots):
    """Return True when path is contained by any root in the iterable."""
    return any(path_is_within_root(path, root) for root in roots if root)


def get_library_roots():
    """
    Get list of all enabled library root paths.

    Returns:
        List of path strings for enabled libraries.
        Falls back to ['/data'] if no libraries configured.
    """
    from core.database import get_libraries
    libraries = get_libraries(enabled_only=True)
    if libraries:
        return [lib['path'] for lib in libraries]
    # Fallback for backwards compatibility
    return ['/data'] if os.path.exists('/data') else []


def get_default_library():
    """
    Get the first enabled library or None.

    Returns:
        Dictionary with library data, or None if no libraries configured.
    """
    from core.database import get_libraries
    libraries = get_libraries(enabled_only=True)
    return libraries[0] if libraries else None


def is_allowed_path(path):
    """Check if path is within any allowed directory (libraries, downloads, temp)."""
    if not path:
        return False

    allowed_roots = list(get_library_roots())

    # Add config directories (WATCH and TARGET)
    from core.config import config
    for key in ('TARGET', 'WATCH'):
        val = config.get("SETTINGS", key, fallback="")
        if val:
            allowed_roots.append(val)

    # Add system temp directory
    allowed_roots.append(tempfile.gettempdir())

    return is_path_in_any_root(path, allowed_roots)


def is_valid_library_path(path):
    """
    Check if a path is within any enabled library.

    Args:
        path: The path to validate

    Returns:
        True if path is within a configured library, False otherwise.
    """
    if not path:
        return False
    return is_path_in_any_root(path, get_library_roots())


def get_library_for_path(path):
    """
    Get the library that contains this path.

    Args:
        path: The path to look up

    Returns:
        Dictionary with library data, or None if path not in any library.
    """
    if not path:
        return None
    from core.database import get_libraries
    for lib in get_libraries(enabled_only=True):
        if path_is_within_root(path, lib['path']):
            return lib
    return None


def is_critical_path(path):
    """
    Check if a path is a critical system path (WATCH, TARGET, or TRASH folders).
    Returns True if the path is critical, False otherwise.
    """
    from core.config import config

    if not path:
        return False

    # Get current watch and target folders from config
    watch_folder = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_folder = config.get("SETTINGS", "TARGET", fallback="/processed")

    # Check if path is exactly a critical folder
    if path_is_within_root(path, watch_folder) and normalize_real_path(path) == normalize_real_path(watch_folder):
        return True

    if path_is_within_root(path, target_folder) and normalize_real_path(path) == normalize_real_path(target_folder):
        return True

    # Check if path is a parent directory of critical folders
    if path_is_parent_of(path, watch_folder) or path_is_parent_of(path, target_folder):
        return True

    # Protect the trash directory root
    try:
        trash_dir = config.get("SETTINGS", "TRASH_DIR", fallback="").strip()
        if not trash_dir:
            cache_dir = config.get("SETTINGS", "CACHE_DIR", fallback="/cache")
            trash_dir = os.path.join(cache_dir, "trash")
        if normalize_real_path(path) == normalize_real_path(trash_dir):
            return True
    except Exception:
        pass

    return False


def get_critical_path_error_message(path, operation="modify"):
    """
    Generate an error message for critical path operations.
    """
    from core.config import config

    watch_folder = config.get("SETTINGS", "WATCH", fallback="/temp")
    target_folder = config.get("SETTINGS", "TARGET", fallback="/processed")

    if path == watch_folder:
        return f"Cannot {operation} watch folder: {path}. Please use the configuration page to change the watch folder."
    elif path == target_folder:
        return f"Cannot {operation} target folder: {path}. Please use the configuration page to change the target folder."
    else:
        return f"Cannot {operation} parent directory of critical folders: {path}. Please use the configuration page to change watch/target folders."
