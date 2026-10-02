function pic2x(in_path, out_path, k)
  % Rotate the image at in_path by k*90 degrees counterclockwise and save it
  % to out_path. Use k = -1 for clockwise. Works in Octave and MATLAB.
  % (Named pic2x, not pic2x' -- in MATLAB, pic2x' would mean "transpose of pic2x".)
  try
    [img, map, alpha] = imread(in_path);
  catch
    [img, map] = imread(in_path);                         % Octave's GIF reader has no alpha output
    alpha = [];
  end

  % Images whose values are all 0/255 (flat graphics, B&W scans, small-palette
  % GIFs) load as logical, which imwrite would save as 1-bit or reject.
  if islogical(img)
    if isempty(map)
      img = uint8(img) * 255;
    else
      img = uint8(img);                                   % palette indices 0/1
    end
  end
  if islogical(alpha)
    alpha = uint8(alpha) * 255;
  end

  % Phone photos are often stored sideways with an EXIF tag saying how to
  % display them. Apply it first so we rotate what the viewer actually saw.
  orientation = exif_orientation(in_path);
  img = apply_orientation(img, orientation);
  alpha = apply_orientation(alpha, orientation);

  img = rot90(img, k);
  alpha = rot90(alpha, k);

  if ~isempty(map)
    imwrite(img, map, out_path);                          % indexed (e.g. GIF)
  elseif ~isempty(alpha)
    imwrite(img, out_path, 'Alpha', alpha);               % keep transparency
  else
    imwrite(img, out_path);
  end
end

function o = exif_orientation(path)
  o = 1;
  try
    info = imfinfo(path);
    if isfield(info, 'Orientation') && ~isempty(info(1).Orientation)
      o = info(1).Orientation;
    end
  catch
  end
end

function img = apply_orientation(img, o)
  % EXIF orientation values 1-8 -> the transform that makes the image upright
  switch o
    case 2, img = fliplr(img);
    case 3, img = rot90(img, 2);
    case 4, img = flipud(img);
    case 5, img = permute(img, [2 1 3]);                  % transpose
    case 6, img = rot90(img, -1);
    case 7, img = rot90(permute(img, [2 1 3]), 2);        % transverse
    case 8, img = rot90(img, 1);
  end
end
