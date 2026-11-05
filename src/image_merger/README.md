# Image Merger

Image Merger hides one or two images inside the pixels of a PNG that simply shows a different picture when opened normally. Each hidden image is encrypted with its own numeric code and can only be recovered with the matching code.

The merged file is an ordinary PNG: no extra data blocks, no trailing bytes, no markers. Tools that list a PNG's contents or scan for appended data find nothing unusual.

## Download the complete application

For the complete, ready-to-use application, download the latest Windows installer from this project's **Releases** page and run it. The installer is the recommended way to get the app; you do not need to install Python or download the source code.

## Merge images

1. Open **Image Merger** and choose **Merge images**.
2. Select **Image A** and enter a numeric code from 3 to 20 digits.
3. Optionally, select **Image B** and enter a different numeric code. Leave both Image B fields empty to merge only one hidden image.
4. Select the **carrier image**. This is the visible picture in the merged PNG.
5. Optionally adjust the hidden-image quality and carrier size (see below), then choose **Check capacity** to see how full the carrier would be.
6. Choose **Create merged PNG...** and select where to save the file.

Keep a record of each code you use. You will need the matching code to recover each image. If you add Image B, its code must be different from Image A's; leading zeros do not make a code different (for example, `007` and `7` count as the same code).

The carrier and hidden images can be selected by clicking the boxes or dragging and dropping files onto them. The app accepts PNG, JPG/JPEG, BMP, GIF, WebP, and TIFF images as inputs. The merged result is always a PNG.

### Choosing a carrier, quality and fill

Hidden images are compressed (WebP) and written into the carrier's pixel values. The more of the carrier you use, the easier the result is to detect, so the app shows a **fill** figure:

| Fill | Detection risk |
| --- | --- |
| up to 10% | low |
| 10% to 25% | medium |
| above 25% | high |

- **Hidden quality** (1-100) sets how strongly the hidden images are compressed. 100 is lossless and needs the most room.
- **Auto-fit** lowers the quality as needed to stay at about 10% fill (never below 10).
- A **larger, more detailed carrier** (a normal photo works best) holds more at the same fill. Flat graphics, screenshots and logos hold very little.
- **Shrink carrier to at most (megapixels)** scales the carrier down before hiding. The merged PNG is about as large as the carrier saved as a lossless PNG, so this is how you keep the output file small. A 12 MP photo is typically 15-25 MB as a PNG; 4-6 MP is much lighter.

As a rough guide, a carrier with N pixels holds about N × 0.03 bytes of hidden data at 10% fill (a 12 MP carrier holds roughly 340 KB).

## Extract an image

1. Choose **Extract image**.
2. Select the merged PNG.
3. Enter the code used for the image you want to recover.
4. Choose **Extract** to preview the result, then **Save As PNG...** to save it.

If the code is wrong, or the PNG contains no hidden image, the app shows a noise image instead of an error. This is deliberate: nobody can tell from the app's behaviour whether a file contains anything, or whether a code was close. If you see noise, check the code and use the original merged PNG.

Files made by earlier versions of Image Merger can still be extracted.

## Important notes

- Keep the merged PNG unchanged. Anything that alters the pixels destroys the hidden images: re-saving as JPEG, resizing, cropping, screenshots, filters, colour conversion, or services that recompress pictures (most chat apps and social networks do when you send a picture rather than a file). Sending the PNG as a file or document, or by email or cloud storage, keeps it intact. Lossless re-saves and metadata removal are fine.
- Hidden images are stored compressed, so the recovered image is a high-quality copy, not a bit-for-bit one, unless you used quality 100.
- Each hidden image is limited to 64 MB of raw pixel data (roughly 21 megapixels for an RGB image; images with transparency have a lower pixel limit) and 16,383 pixels on its longest side.
- The code is the only secret. Hidden data is encrypted with a key derived from it, but a short code can be guessed by trying every possibility. Use at least 8 digits, and preferably 12 or more, for anything that matters. This hides images from casual and automated detection; it is not a guarantee against a determined expert with the original carrier picture.

## Troubleshooting

**I only see noise after extracting.** The code is wrong, or the file has no hidden image, or the PNG was changed after merging (see the notes above). Try the original merged PNG and double-check the code, remembering that leading zeros are ignored.

**The app says the carrier is too small or has too little detail.** Choose a larger or more detailed carrier, lower the hidden-image quality, or hide a smaller image.

**The merged PNG is very large.** Its size follows the carrier. Use **Shrink carrier to at most (megapixels)** or a smaller carrier.

## Running from source

```
pip install pillow numpy tkinterdnd2 cryptography
python -m image_merger.main              # opens the app
python -m image_merger.main merge --help # command-line use
python -m unittest image_merger.test_stealth -v
```
