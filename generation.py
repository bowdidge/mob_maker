#!/usr/bin/python3
#
# Generative AI artifact generation code.
#
# This calls different AI services for different kinds of generation -
# creating descriptions of figures, creating images from descriptions, and
# creating 3d models from images.

import logging
import os
import subprocess
from io import BytesIO

from google import genai
from google.genai.types import GenerateContentConfig, Modality
from PIL import Image

logger = logging.getLogger(__name__)

def generate_image_from_prompt(prompt, filename='generated_image.png'):
    """Create a reference sheet image based on provided prompt.

    Uses Google Gemini GenAPI in the cloud.

    Returns filename of generated image.
    """
    
    # 1. Initialize the client with your API key
    api_key = os.getenv('GENAI_API_KEY')
    if not api_key:
        logger.warning('GENAI_API_KEY environment variable not found')
        return []
    client = genai.Client(api_key=api_key)

    # 2. Call generate_content using the image model 
    # explicitly requesting the IMAGE response modality
    logger.warning('Generating image for %s', prompt)
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=GenerateContentConfig(
            response_modalities=[Modality.TEXT, Modality.IMAGE]))
    

    # 3. Iterate through response parts to find and save the image data
    image_saved = False
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            # Load the raw bytes into a PIL Image and save
            image = Image.open(BytesIO(part.inline_data.data))
            image.save(filename)
            image_saved = True
            break

    logger.warning('Done generating image for %s', prompt)
    if not image_saved:
        logger.warning('No image data found in the response; text output: %s', response.text)
        return None
    return filename

def generate_prompts(theme):
    """Generate several descriptions of reasonable figures for the theme.

    Returns list of descriptions usable for prompts to generate an image.

    Current implementation uses Google Gemini to generate the prompts.
    """
    prompt_format = ('You are generating descriptions of a collection of 3d '
                     'miniature figures for a model railroad. %s  Generate '
                     'five descriptions of realistic figures for that theme. '
                     'For each figure, describe the age, gender, body shape,'
                     'dress, and pose of the figure.  For each description, '
                     'provide only a single line of text, one line per '
                     'description, with each line starting with a two or '
                     'three word summary ("old woman dress"), a colon, and '
                     'the description.')

    figure_description_prompt = prompt_format % (theme)

    api_key = os.getenv('GENAI_API_KEY')
    if not api_key:
        logger.warning('GENAI_API_KEY environment variable not found')
        return []

    client = genai.Client(api_key=api_key)
    chat = client.chats.create(model='gemini-3.8-flash')
    response = chat.send_message(figure_description_prompt)

    response_text = ''
    for part in response.candidates[0].content.parts:
        response_text += part.text
    
    figure_prompts = response_text.split('\n')
    return figure_prompts

def generate_stl_from_image(filename):
    """Create an STL file for a figure from a reference sheet image.

    Needs to split image into (front, side, back), then send to Tripo3D.
    """
    # TODO(bowdidge): Implement.
    pass
