# toolbin
A place for utility scripts that should be saved.

## filenum.py

Number files in ascending filesystem-byte (ASCII for ASCII names) order:

```sh
python3 filenum.py tag 1 file2.txt file1.txt
# file1.txt becomes file1_tag_1.txt, then file2.txt becomes file2_tag_2.txt
python3 filenum.py "" 0 file.txt
# file.txt becomes file_0.txt
```

The tag and nonnegative starting number are required; filenames are optional.
The number is inserted before the final extension, or appended if there is no
extension. Existing destinations, duplicate inputs, and non-files are rejected
before any renames. Do not modify the files concurrently while the command runs.
