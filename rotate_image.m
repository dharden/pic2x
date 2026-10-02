function rotate_image(in_path, out_path, k)
  % Rotate the image at in_path by k*90 degrees counterclockwise and save it
  % to out_path. Use k = -1 for clockwise. Works in Octave and MATLAB.
  try
    [img, map, alpha] = imread(in_path);
  catch
    [img, map] = imread(in_path);                         % Octave's GIF reader has no alpha output
    alpha = [];
  end
  img = rot90(img, k);

  if ~isempty(map)
    imwrite(img, map, out_path);                          % indexed (e.g. GIF)
  elseif ~isempty(alpha)
    if islogical(alpha)                                   % fully opaque/clear alpha loads as 1-bit
      alpha = uint8(alpha) * 255;
    end
    if islogical(img)
      img = uint8(img) * 255;
    end
    imwrite(img, out_path, 'Alpha', rot90(alpha, k));     % keep transparency
  else
    imwrite(img, out_path);
  end
end
