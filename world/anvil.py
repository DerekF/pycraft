import os
from os import path
import gzip
import zlib
import io
import numpy
import math
import bisect
import arrow
from . import nbt
from . import chunk

__null_sector = bytes(4096)

class Sector(object):
    __slots__ = ('offset', 'count')
    def __init__(self, offset, count):
        self.offset = offset
        self.count = count
    
    @property
    def end(self) -> int:
        return self.offset + self.count
    
    @property
    def size(self) -> int:
        return self.count * 4096
    
    @property
    def file_offset(self):
        return self.offset * 4096
    
    def __lt__(self, other):
        return (self.offset + self.count) <= other.offset
    
    def __gt__(self, other):
        return self.offset >= (other.offset + other.count)
    
    def __eq__(self, other):
        return self.offset == other.offset and self.count == other.count
    
    def intersects(self, other):
        if self.offset <= other.offset and self.end > other.offset:
            return True
        if other.offset <= self.offset and other.end > self.offset:
            return True
        return False
    
    def to_bytes(self):
        with io.BytesIO() as buff:
            buff.write(self.offset.to_bytes(3, 'big', signed=False))
            buff.write(self.count.to_bytes(1, 'big', signed=False))
            return buff.getvalue()
    
    def __repr__(self):
        return f'Sector(offset={self.offset}, count={self.count})'

class RegionFile:

    __slots__ = ('filename','chunks','sectors')

    def __init__(self, filename : str):
        self.filename = filename
        self.chunks = numpy.ndarray(shape=(1024), dtype=numpy.object_)
        self.sectors = None
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        if path.isfile(filename):
            with open(self.filename, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                if size < 8192 or (size % 4096) != 0:
                    raise Exception('Invalid region file!')

                self.sectors = [Sector(0, 2)]
                for i in range(1024):
                    offset = int.from_bytes(f.read(3), byteorder='big', signed=False)
                    sector_count = int.from_bytes(f.read(1), byteorder='big', signed=False)
                    if offset >= 2 and sector_count > 0:
                        sector = Sector(offset, sector_count)
                        self.chunks[i] = sector
                        bisect.insort(self.sectors, sector)
                    else:
                        self.chunks[i] = None
    
    def write_sector(self, sector, index):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        if path.isfile(self.filename):
            with open(self.filename, 'wb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                if size < 8192 or (size % 4096) != 0:
                    raise Exception('Invalid region file!')
                
                f.seek(index * 4)
                f.write(sector.offset.to_bytes(3, byteorder='big', signed=False))
                f.write(sector.count.to_bytes(1, byteorder='big', signed=False))
                self.chunks[index] = sector
                bisect.insort(self.sectors, sector)

    def delete_sector(self, index, zero_data = False):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        if 0 <= index < 1024:
            sect = self.chunks[index]
            if sect != None:
                with open(self.filename, 'rb') as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(0)
                    if size < 8192 or (size % 4096) != 0:
                        raise Exception('Invalid region file!')
                    
                    f.seek(index * 4)
                    f.write(b'\x00\x00\x00\x00')
                    self.chunks[index] = None
                    sect_ind = bisect.bisect_left(self.sectors, sect)
                    if ind != len(self.sectors) and self.sectors[ind] == sect:
                        del self.sectors[ind]
                    if zero_data:
                        f.seek(sect.offset * 4096)
                        for _ in range(sect.count):
                            f.write(__null_sector)
    
    def write_timestamp(self, index):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        if path.isfile(self.filename):
            with open(self.filename, 'wb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                if size < 8192 or (size % 4096) != 0:
                    raise Exception('Invalid region file!')
                utc = arrow.utcnow()
                f.seek(index * 4 + 4096)
                f.write(utc.int_timestamp.to_bytes(4, 'big', False))

    def read_timestamp(self, index):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        if path.isfile(self.filename):
            with open(self.filename, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(0)
                if size < 8192 or (size % 4096) != 0:
                    raise Exception('Invalid region file!')
                f.seek(index * 4 + 4096)
                return arrow.Arrow.fromtimestamp(int.from_bytes(f.read(4), 'big', False))
    
    def get_free(self, size_in_bytes,insert=True):
        sector_count = math.ceil(size_in_bytes / 4096)
        for i in range(0, len(self.sectors) - 1):
            dist_between = self.sectors[i+1].offset - self.sectors[i].end
            if dist_between >= sector_count:
                sector = Sector(self.sectors[i].end, sector_count)
                if insert:
                    bisect.insort(self.sectors, sector)
                return sector
        #   We reached the end of the file, so we'll need to append some sectors.
        end_sector = self.sectors[-1]
        sector = Sector(end_sector.end, sector_count)
        if insert:
            bisect.insort(self.sectors, sector)
        return sector


    def read_chunk(self, offsetX : int, offsetZ : int) -> chunk.Chunk:
        tag, _ = self.read_chunk_tag(offsetX, offsetZ)
        return chunk.Chunk(tag)
    
    def write_chunk_tag(self, offsetX : int, offsetZ : int, chunk_tag : nbt.nbt_tag):
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        chunk_data = zlib.compress(nbt.dump(chunk_tag))
        ind = ((offsetX & 31) + (offsetZ & 31) * 32)
        if self.chunks[ind] != None:
            self.delete_sector(ind)
        free = self.get_free(len(chunk_data)+5,True)
        #ensure the capacity of our file:
        with open(self.filename, 'wb') as f:
            offset = free.offset * 4096
            size = free.count * 4096
            data_size = len(chunk_data) + 5
            pad_bytes = size - data_size
            f.seek(offset)
            f.write((len(chunk_data) + 1).to_bytes(4, 'big', False))
            f.write(b'\x02')
            f.write(bytes(pad_bytes))

    
    def read_chunk_tag(self, offsetX : int, offsetZ : int) -> nbt.nbt_tag:
        """
        Reads the chunk (decompressed) from the region file and returns the NBT.
        """
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        decompressed_data = None
        ind = ((offsetX & 31) + (offsetZ & 31) * 32)

        with open(self.filename,'rb') as f:
            f.seek(ind * 4)
            chunk_offset = int.from_bytes(f.read(3),'big')
            if chunk_offset == 0:
                return
            f.seek(chunk_offset * 4096)
            data_length = int.from_bytes(f.read(4),'big')
            compression_type = int.from_bytes(f.read(1),'big')
            # 1 GZip, 2 Zlib, 3 uncompressed
            if compression_type == 2:
                return nbt.load(zlib.decompress(f.read(data_length-1)))
            elif compression_type == 1:
                return nbt.load(gzip.decompress(f.read(data_length-1)))
            elif compression_type == 3:
                return nbt.load(f.read(data_length-1))
    
    def read_chunk_raw(self, offsetX : int, offsetZ : int) -> bytes:
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        decompressed_data = None
        ind = ((offsetX & 31) + (offsetZ & 31) * 32)

        with open(self.filename,'rb') as f:
            f.seek(ind * 4)
            chunk_offset = int.from_bytes(f.read(3),'big')
            if chunk_offset == 0:
                return
            f.seek(chunk_offset * 4096)
            data_length = int.from_bytes(f.read(4),'big')
            compression_type = int.from_bytes(f.read(1),'big')
            # 1 GZip, 2 Zlib, 3 uncompressed
            if compression_type == 2:
                return zlib.decompress(f.read(data_length-1))
            elif compression_type == 1:
               return gzip.decompress(f.read(data_length-1))
            elif compression_type == 3:
                return f.read(data_length-1)
    
    def has_chunk(self, offsetX : int, offsetZ : int) -> bool:
        if not os.path.exists(self.filename):
            raise FileNotFoundError(self.filename)
        ind = ((offsetX & 31) + (offsetZ & 31) * 32)
        with open(self.filename, 'rb') as f:
            f.seek(ind * 4)
            return int.from_bytes(f.read(3), 'big') != 0