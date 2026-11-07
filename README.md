Pipeline for creating large sets of 3d printed figures for background figures.

+ Summary

This provides a semi-automated pipeline for generating 3d printed
figures for model railroads.  It uses generative AI tools to propose
potential figures:
 * Google Gemini chatbot to come up with random figure descriptions
 * Gemini image generation to turn descriptions into reference images.
 * Tripo3D to turn reference images into STL files for the figures.

The intended goal is to run through many possible options, then refine
and narrow to the figures that work.

+ Problem

Getting period- and setting-accurate figures for a model railroad is a
challenge.  Commercial figures are scarce and only have a few eras.  Preiser's
figures explicitly look like 1960's Europe.  For specific settings such
as canneries, sawmills, or city streets, getting a sufficiently diverse
mix of figures for a narrow setting is hard.

Regular figures often go for very specific looks for photos or foreground
scenes.  For filling out the masses of people moving or acting in plausible
ways, we don't need that much detail.

MobMaker automates creating a large set of figures on a particular theme.
It uses AI tools to generate an assortment of figures which the creator
can pick from.  MobMaker allows you to provide a generic theme and setting
(1930s cannery workers, 1950s office workers).  It will use large language
models to generate a set of plausible characters and poses.  You can
indicate which prompts seem interesting, and send those to an AI image
generator to create a "reference image" - a front side and back view.  If
the image seems appropriate and correctly posed, the image can be sent
to tripo3d.ai to make an STL File.

If any of the generated results is uninteresting, they can be removed.
A description may be implausible or uninteresting; a figure may be boring,
unrealistic, or not correctly capture the look intended, tripo3d.ai may
not generate a decent model for a description.  At any time, the user
can either modify the description to try to get a different image, rerun
the image generation, or re-send the picture to tripo3d.ai.

+ Usage

The web interface has a large box at the top for a description, and can
also take a user-provided photo of potential individuals.

As a first step, the description is sent into an LLM chatbot to generate a
set of potential figure descriptions - probably on the order of 5-10.
These are shown to the user.  The user can choose which ones should have
images generated, and which can be removed.  Once the images are generated,
the user can select which should be sent to produce a 3d model.

Potential user interface:
pane at the top for giving a theme, and button for generating an additional
set of descriptions.
For each description, there are three sets of UI:
*  Left cells give control - delete (garbage can), mark as interesting (star),
  uninteresting (frown face),
  regenerate image,
  and send/regenerate to Tripo3d for figure generation (printer).
* center cells give the description.  This is editable by clicking / refining.
* Right cells give the image created from the description.

Potential file layout:

* There is one folder for each mob.  Top level file
mob_description.json contains the current desscription of the mob,
list of recent descriptions, and additional information (things to
automatically reject?), and list of all descriptions, filenames for
images creates, and filenames for STL files produced.  The rest of the
folder contains all the image and STL files produced from the descriptions
as requested by the user.

+ Running

The web server serves the UI from ``web/`` and exposes a small JSON REST
API that reads/writes the mob's ``mob_description.json`` on disk.

    % export GENAI_API_KEY [insert your Google GenAI key here]
    % python3 server.py            # defaults to port 8000, localhost only
    % python3 server.py --port 9000 --mob-dir /path/to/demo_mob    # pick a different port

By default it serves the bundled sample mob in ``demo_mob/``.  To serve a
different mob directory (or a specific ``mob_description.json``), set the
``MM_MOB_DIR`` environment variable:

    % MM_MOB_DIR=~/mobs/oakland_pier
    % python3 server.py

Then open http://127.0.0.1:8000/ in a browser.  Changes you save in the UI
(via the REST API) are written straight back to the mob's
``mob_description.json``.

Notes on what is / isn't wired up:

* Generating reference images (``POST /api/figures/<id>/image``) uses the
  Google GenAI image API, so it needs the Google GenAI SDK installed and a
  valid key in the enviroment variable GENAI_API_KEY.
* ``POST /api/figures/<id>/stl`` is exercised by the UI but currently writes a
  placeholder STL; the real Tripo3D client is not yet integrated.
* Both "Generate descriptions" buttons call ``POST /api/mob/descriptions``,
  which builds new figure descriptions from the theme.  This also needs the
  Google GenAI SDK and a key in the GENAI_API_KEY environment variable.

+ Costs

Google image generation requires an API account tied to a Google AI API account
with money.  As of 11/2026, these seem to be in the range of 10c each.

Tripo3d.ai is a flat $20/month for about 50 figures a month.

+ Features to add

The following features still need to be added.
* Support for rendering STL via web interface.
* Splitting reference images into multiple images.
* Combining the theme prompt with the figure prompt so the context is understood.