"""Long-running generation steps for MobMaker.

Each function takes a :class:`mob_file.Mob` and a :class:`mob_file.Figure`,
performs the heavy (network) work, and updates the figure's ``image_file``
/ ``stl_file`` accordingly.  The web server calls these from its REST API
endpoints and is responsible for saving the mob back to disk (the load /
mutate / save cycle happens on the server side, under a lock).

Conventions
-----------
* Images are written to the mob folder as ``<name>.png``.
* STL models are written to the mob folder as ``<name>.stl``.

  ``<name>`` is the figure's slugified ``name`` field (falling back to
  the figure's id if the name is empty).  Names are expected to be short
  and reasonably unique per mob, since two figures sharing a name would
  write to the same file.
"""

import re

import mob_file


def _figure_stem(fig):
    """Slugify a figure's name for use as a short file stem."""
    base = ((fig.name or "").strip() or fig.id or "figure")
    stem = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return stem or "figure"


def generate_image(mob, fig):
    """Generate a reference image for *fig* and record it in the mob.

    Uses the generative AI image generation, then
    writing the resulting image into the mob folder.  On success sets
    ``fig.image_file`` to the image's basename and returns that basename.

    Args:
        mob: the mob the figure belongs to (used to resolve file paths).
        fig: the figure to generate an image for.

    Returns:
        The basename of the image file that was written.

    Raises:
        mob_file.MobFileError: if the image could not be generated
            (missing SDK / API key, or the generator returned nothing).
    """
    prompt = fig.prompt()
    name = "%s.png" % _figure_stem(fig)
    out_path = mob.image_path(name)
    if out_path is None:
        raise mob_file.MobFileError(
            "mob has no directory; cannot write image file")
    # The GenAI SDK is an optional dependency, so import lazily.
    try:
        from generation import generate_image_from_prompt
    except ImportError as e:
        raise mob_file.MobFileError(
            "image generation unavailable (is the Google GenAI SDK "
            "installed?): %s" % e)
    result = generate_image_from_prompt(prompt, str(out_path))
    if not result:
        raise mob_file.MobFileError("image generation returned no image")
    fig.image_file = name
    return name


def make_filename_prefix(name):
    """Convert a short phrase into a reasonable filename prefix."""
    return name.replace(' ', '_').lower()
    
def generate_descriptions(mob):
    """Generate a fresh set of figure descriptions from the mob's theme.

    The new figures keep their place in ``mob.figures``; the caller
    (the web server) is responsible for saving the mob back to disk.

    Args:
        mob: the mob whose ``current_theme`` drives generation.

    Returns:
        The number of figures added.

    Raises:
        mob_file.MobFileError: if the mob has no ``current_theme`` or the
            image generation module cannot be imported.
    """
    theme = (mob.current_theme or "").strip()
    if not theme:
        raise mob_file.MobFileError(
            "mob has no current_theme; set one first")
    # The GenAI SDK is an optional dependency, so import lazily.
    try:
        from generation import generate_prompts
    except ImportError as e:
        raise mob_file.MobFileError(
            "description generation unavailable (is the Google GenAI SDK "
            "installed?): %s" % e)
    added = 0
    for line in generate_prompts(theme):
        line = str(line).strip()
        if ':' in line:
            (short_name, description) = line.split(':')
            short_name = make_filename_prefix(short_name)
        else:
            short_name = mob_file.potential_name(line)
            description = line
            
        if not line:
            continue
        # from_dict treats a plain string as the figure's description and
        # derives a default name from its first line (renameable in the UI).
        mob.figures.append(mob_file.Figure.from_dict({'name': short_name,
                                                      'description': description},
                                                     mob=mob))
        added += 1
    return added


def generate_stl(mob, fig):
    """Produce an STL model for *fig* from its reference image.

    In the full pipeline this uploads ``fig.image_file`` to Tripo3D and
    downloads the resulting mesh.  That client is not yet integrated, so
    this step currently validates its prerequisites (a reference image
    must already exist) and writes a valid placeholder STL so the whole
    flow is testable.  Replace the placeholder with the real Tripo3D call
    when it is available.

    Args:
        mob: the mob the figure belongs to (used to resolve file paths).
        fig: the figure to produce an STL for.

    Returns:
        The basename of the STL file that was written.

    Raises:
        mob_file.MobFileError: if the figure has no reference image yet,
            or the STL file cannot be written.
    """
    if not fig.image_file:
        raise mob_file.MobFileError(
            "no reference image for %r; generate one first"
            % (fig.name or fig.id))
    name = "%s.stl" % _figure_stem(fig)
    out_path = mob.image_path(name)
    if out_path is None:
        raise mob_file.MobFileError("mob has no directory; cannot write STL")

    # TODO: call the real Tripo3D client here -- upload fig.image_file and
    #       download the mesh to out_path.  For now a placeholder box keeps
    #       the round-trip working so the UI / REST plumbing can be tested.
    _write_placeholder_stl(out_path)

    fig.stl_file = name
    return name


def _write_placeholder_stl(path):
    """ Write a small, valid ASCII STL solid (a unit box) at ``path``. """
    # 8 corner vertices of a cube spanning [-1, 1] on all axes.
    v = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    # 12 triangles (each face quad split into two) by vertex index.
    faces = [
        (0, 1, 2), (0, 2, 3),  # bottom
        (4, 6, 5), (4, 7, 6),  # top
        (0, 4, 5), (0, 5, 1),  # front
        (1, 5, 6), (1, 6, 2),  # right
        (2, 6, 7), (2, 7, 3),  # back
        (3, 7, 4), (3, 4, 0),  # left
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("solid mobmaker_placeholder\n")
        for (a, b, c) in faces:
            p0, p1, p2 = v[a], v[b], v[c]
            n = (0, 0, 1)  # flat approximate normal (fine for a placeholder)
            f.write("  facet normal %g %g %g\n" % n)
            f.write("    outer loop\n")
            for p in (p0, p1, p2):
                f.write("      vertex %g %g %g\n" % p)
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write("endsolid mobmaker_placeholder\n")
    return path
