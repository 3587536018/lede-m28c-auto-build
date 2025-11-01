#!/bin/bash -x
cd lede
echo "update feeds"
./scripts/feeds update -a || { echo "update feeds failed"; exit 1; }
echo "install feeds"
./scripts/feeds install -a || { echo "install feeds failed"; exit 1; }
#./scripts/feeds update qmodem
./scripts/feeds install -a -f -p qmodem || { echo "install qmodem feeds failed"; exit 1; }
cat ../m28c.config > .config
echo "make defconfig"
make defconfig || { echo "defconfig failed"; exit 1; }
echo "diff initial config and new config:"
diff ../m28c.config .config
echo "make download"
make download -j8 || { echo "download failed"; exit 1; }
echo "make lede"
make V=s -j1 || { echo "make failed"; exit 1; }
