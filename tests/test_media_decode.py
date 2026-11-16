from pathlib import Path
import tempfile
import unittest
import hashlib
from PIL import Image, ImageCms
import numpy as np
import media_decode


class PixelDecodeTests(unittest.TestCase):
    def test_transparency_survives_icc_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'transparent.png';output=root/'input.jpg'
            image=Image.new('RGBA',(800,600),(0,0,0,0))
            image.save(source,icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes())
            before=hashlib.sha256(source.read_bytes()).digest()
            media_decode.render_photo(source,output)
            with Image.open(output) as decoded:
                self.assertTrue(min(decoded.getpixel((0,0)))>245)
                self.assertFalse(decoded.getexif())
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)

    def test_sixteen_bit_gray_keeps_midtones(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'gray.png';output=Path(folder)/'input.jpg'
            pixels=np.zeros((600,900),dtype=np.uint16)
            pixels[:,300:600]=32768;pixels[:,600:]=65535
            Image.fromarray(pixels).save(source)
            media_decode.render_photo(source,output)
            with Image.open(output) as decoded:
                self.assertLess(decoded.getpixel((150,300))[0],10)
                self.assertTrue(120<=decoded.getpixel((450,300))[0]<=135)
                self.assertGreater(decoded.getpixel((750,300))[0],245)

    def test_orientation_is_applied_and_metadata_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'oriented.jpg';output=Path(folder)/'input.jpg'
            exif=Image.Exif();exif[274]=6;exif[315]='Synthetic fixture'
            Image.new('RGB',(400,800),'blue').save(source,exif=exif)
            media_decode.render_photo(source,output)
            with Image.open(output) as decoded:
                self.assertEqual(decoded.size,(800,400));self.assertFalse(decoded.getexif())


if __name__=='__main__':unittest.main()
