These three unmodified source files are regression fixtures from
https://github.com/coolsnowwolf/lede/tree/8e247d214.

Their relative paths mirror the upstream repository. Tests run offline and
verify removal of the upstream shared factory password, preservation of the
base shadow file, and disabled defaults in the actual mac80211 generator.
The factory password hash in the defaults fixture is public upstream test data;
it is removed from the source used to build the public firmware.
