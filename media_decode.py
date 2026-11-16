"""Read-only local media decoding; derived model inputs have no EXIF."""
from pathlib import Path
import io
import media_index as app
RAW_EXT={'.rw2', '.cr2', '.dng', '.orf', '.srw', '.arw', '.pef', '.nef', '.cr3', '.raf'}

def prepare_pixels(image):
    from PIL import Image, ImageOps, ImageCms
    image=ImageOps.exif_transpose(image)
    info=dict(image.info)
    if image.mode.startswith('I;16'):
        import numpy as np
        pixels=np.rint(np.asarray(image).astype(np.float32)/257).clip(0,255).astype(np.uint8)
        image=Image.fromarray(pixels)
        image.info.update(info)
    elif image.mode in {'I','F'}:
        raise app.IndexErrorUser('Unspecified high-dynamic-range photo encoding requires review')
    alpha=None
    if image.mode in {'RGBA','LA'} or (image.mode=='P' and 'transparency' in image.info):
        alpha=image.convert('RGBA').getchannel('A')
    profile=image.info.get('icc_profile')
    converted=False
    if profile:
        color=image.getchannel('L') if image.mode=='LA' else image.convert('RGB') if image.mode in {'RGBA','P'} else image
        image=ImageCms.profileToProfile(color,ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
        converted=True
    image=image.convert('RGB')
    if alpha is not None:
        canvas=Image.new('RGB',image.size,'white');canvas.paste(image,mask=alpha);image=canvas
    return image,converted


def render_photo(source: Path, destination: Path):
    from PIL import Image, ImageOps, ImageCms
    metadata = {}
    if source.suffix.casefold() in RAW_EXT:
        import rawpy
        with rawpy.imread(str(source)) as raw:
            pixels = raw.postprocess(use_camera_wb=True, output_bps=8, output_color=rawpy.ColorSpace.sRGB)
        image = Image.fromarray(pixels)
        metadata['decoder'] = 'rawpy-libraw'
    else:
        try:
            image = Image.open(source)
            metadata['frame_count'] = getattr(image, 'n_frames', 1)
            metadata['animated'] = source.suffix.casefold() in {'.gif', '.webp', '.png'} and bool(getattr(image, 'is_animated', False))
            metadata['auxiliary_image_frames'] = source.suffix.casefold() in {'.jpg', '.jpeg'} and metadata['frame_count'] > 1
            image.load()
            metadata['decoder'] = 'Pillow'
        except Exception:
            if source.suffix.casefold() not in {'.heic', '.heif'}:
                raise
            converted = destination.with_suffix('.converted.jpg')
            app.run_checked(['sips', '-s', 'format', 'jpeg', str(source), '--out', str(converted)], 'HEIC decode')
            image = Image.open(converted)
            image.load()
            metadata['decoder'] = 'sips-Pillow'
    metadata['source_dimensions'] = list(image.size)
    image,converted=prepare_pixels(image)
    if converted:metadata['color_conversion']='ICC-to-sRGB'
    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, 'JPEG', quality=95)
    metadata['review_dimensions'] = list(image.size)
    metadata['embedded_exif_removed'] = True
    return metadata

