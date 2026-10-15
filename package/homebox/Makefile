include $(TOPDIR)/rules.mk

PKG_NAME:=homebox
PKG_VERSION:=0.0.0_pre2020062901
PKG_RELEASE:=4
PKG_SOURCE:=homebox-2020062901-linux-arm64.tar.gz
PKG_SOURCE_URL:=https://github.com/XGHeaven/homebox/releases/download/v0.0.0-dev.2020062901/
PKG_SOURCE_URL_FILE:=server-linux-arm64.tar.gz
PKG_HASH:=c42cf7f58ba57e0f6f4209ba134f827074ba7a0099574724ff1d4d3e7d569d27

include $(INCLUDE_DIR)/package.mk

define Package/homebox
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Homebox LAN speed test
  URL:=https://github.com/XGHeaven/homebox
  DEPENDS:=@aarch64
endef

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
	tar -xzf $(DL_DIR)/$(PKG_SOURCE) -C $(PKG_BUILD_DIR)
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/homebox/conffiles
/etc/config/homebox
endef

define Package/homebox/install
	$(INSTALL_DIR) $(1)/usr/bin $(1)/etc/config $(1)/etc/init.d $(1)/etc/uci-defaults
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/server-linux-arm64 $(1)/usr/bin/homebox
	$(INSTALL_CONF) ./files/homebox.config $(1)/etc/config/homebox
	$(INSTALL_BIN) ./files/homebox.init $(1)/etc/init.d/homebox
	$(INSTALL_BIN) ./files/homebox.uci-default $(1)/etc/uci-defaults/homebox
endef

$(eval $(call BuildPackage,homebox))
