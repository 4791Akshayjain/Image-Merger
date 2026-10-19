# Image Merger

Image Merger stores two images inside a PNG that displays a separate picture when opened normally. Each hidden image is scrambled using its own numeric code and can be recovered with the matching code.

## Download the complete application

For the complete, ready-to-use application, download the latest Windows installer from this project's **Releases** page and run it. The installer is the recommended way to get the app; you do not need to install Python or download the source code.

## Merge images

1. Open **Image Merger** and choose **Merge images**.
2. Select **Image A** and enter a numeric code from 3 to 20 digits.
3. Select **Image B** and enter a different numeric code.
4. Select the **carrier image**. This is the visible picture in the merged PNG.
5. Choose **Create merged PNG...** and select where to save the file.

Keep a record of both codes. You will need the matching code to recover each image. The two codes must be different; leading zeros do not make a code different (for example, `007` and `7` count as the same code).

The carrier and hidden images can be selected by clicking the boxes or dragging and dropping files onto them. The app accepts PNG, JPG/JPEG, BMP, GIF, WebP, and TIFF images as inputs. The merged result is always a PNG.

## Extract an image

1. Choose **Extract image**.
2. Select the merged PNG.
3. Enter the code used for the image you want to recover.
4. Choose **Extract** to preview the result, then **Save As PNG...** to save it.

## Important notes

- Keep the merged PNG unchanged. Editing, recompressing, or re-saving it in another app may remove the hidden image data. Share the original PNG file when possible.
- The merged PNG can be much larger than the carrier image because it contains the pixel data for both hidden images.
- Each hidden image is limited to 64 MB of raw pixel data (roughly 21 megapixels for an RGB image; images with transparency have a lower pixel limit).
- The numeric codes scramble the hidden image data; this app does not encrypt it. Do not use it as a replacement for encryption when you need strong confidentiality.

## Troubleshooting

**The app says the PNG has no hidden image data.** The file may not have been created by Image Merger, or it may have been changed or recompressed after merging. Try the original merged PNG.

**The recovered image does not look right.** Check that you entered the same code used when that image was added. Remember that leading zeros are ignored when comparing codes.
