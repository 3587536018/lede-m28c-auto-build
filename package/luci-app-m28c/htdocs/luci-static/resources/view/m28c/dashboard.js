'use strict';
'require view';
'require rpc';
'require poll';
'require ui';

var callBoard = rpc.declare({ object: 'system', method: 'board' });
var callInfo = rpc.declare({ object: 'system', method: 'info' });
var callMounts = rpc.declare({ object: 'luci', method: 'getMountPoints', expect: { result: [] } });

// A load average is not CPU utilisation. Keep its unit and all three windows visible.
function bytes(value) {
	if (typeof value !== 'number' || !isFinite(value) || value < 0)
		return '暂不可用';
	var units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'], n = 0;
	while (value >= 1024 && n < units.length - 1) { value /= 1024; n++; }
	return value.toFixed(n > 1 ? 1 : 0) + ' ' + units[n];
}

function uptime(value) {
	if (typeof value !== 'number' || !isFinite(value) || value < 0)
		return '暂不可用';
	var days = Math.floor(value / 86400), hours = Math.floor(value / 3600) % 24;
	return (days ? days + ' 天 ' : '') + hours + ' 时 ' + Math.floor(value / 60) % 60 + ' 分';
}

function icon(name) {
	var paths = {
		clock: 'M12 8v4l3 2 M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18',
		chip: 'M8 3v3m4-3v3m4-3v3M8 18v3m4-3v3m4-3v3M3 8h3m-3 4h3m-3 4h3m12-8h3m-3 4h3m-3 4h3M6 6h12v12H6zM9 9h6v6H9z',
		memory: 'M3 6h18v12H3zM7 9v4m5-4v4m5-4v4M7 16v4m5-4v4m5-4v4',
		disk: 'M5 4h14l3 11v5H2v-5L5 4zM2 15h20M6 17.5h2m3 0h1',
		signal: 'M5 20v-4m5 4V12m5 8V8m5 12V4',
		globe: 'M3 12h18M12 3c5 5 5 13 0 18c-5-5-5-13 0-18M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18',
		store: 'M4 10v10h16V10M2 10l2-6h16l2 6M2 10c0 3 5 3 5 0c0 3 5 3 5 0c0 3 5 3 5 0c0 3 5 3 5 0M9 20v-6h6v6',
		box: 'M12 2l10 5v10l-10 5l-10-5V7l10-5zM2 7l10 5l10-5M12 12v10M7 4.5l10 5',
		folder: 'M3 5h7l2 3h9v12H3V5z',
		network: 'M9 3h6v6H9zM3 15h6v6H3zM15 15h6v6h-6zM12 9v3M6 15v-3h12v3',
		arrow: 'M5 12h14m-5-5l5 5l-5 5'
	};
	var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
	svg.setAttribute('viewBox', '0 0 24 24');
	svg.setAttribute('fill', 'none');
	svg.setAttribute('stroke', 'currentColor');
	svg.setAttribute('stroke-width', '1.6');
	svg.setAttribute('stroke-linecap', 'round');
	svg.setAttribute('stroke-linejoin', 'round');
	svg.setAttribute('aria-hidden', 'true');
	var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
	path.setAttribute('d', paths[name] || paths.network);
	svg.appendChild(path);
	return svg;
}

function findRoute(menu, candidates) {
	for (var i = 0; i < candidates.length; i++) {
		var path = candidates[i].split('/'), node = menu;
		for (var j = 0; node && j < path.length; j++)
			node = node.children && node.children[path[j]];
		if (node && node.satisfied !== false)
			return path;
	}
	return null;
}

function link(menu, title, candidates, className) {
	var route = findRoute(menu, candidates);
	return route ? E('a', { href: L.url.apply(L, route), 'class': className || 'm28c-link' }, [title, icon('arrow')]) : null;
}

function meter(value, title) {
	return E('div', { 'class': 'm28c-meter', 'aria-label': title }, [
		E('span', { style: 'width:' + Math.max(0, Math.min(100, value || 0)).toFixed(1) + '%' })
	]);
}

function card(title, value, detail, symbol, percentage) {
	return E('article', { 'class': 'm28c-stat' }, [
		E('div', { 'class': 'm28c-stat-title' }, [E('span', {}, title), icon(symbol)]),
		E('strong', { 'class': 'm28c-stat-value' }, value),
		E('div', { 'class': 'm28c-stat-detail' }, detail),
		percentage == null ? E('div', { 'class': 'm28c-stat-line' }) : meter(percentage, title + ' ' + percentage.toFixed(0) + '%')
	]);
}

function renderStats(info, mounts) {
	mounts = Array.isArray(mounts) ? mounts : [];
	var mem = info.memory || {}, memoryUsed = null, memoryPercent = null;
	if (mem.total > 0) {
		var available = typeof mem.available === 'number' ? mem.available :
			(typeof mem.free === 'number' ? mem.free + (mem.buffered || 0) + (mem.cached || 0) : null);
		if (available != null) {
			memoryUsed = Math.max(0, Math.min(mem.total, mem.total - available));
			memoryPercent = memoryUsed / mem.total * 100;
		}
	}
	var storage = mounts.filter(function(m) { return m.mount === '/overlay'; })[0] ||
		mounts.filter(function(m) { return m.mount === '/'; })[0];
	var storagePercent = storage && storage.size > 0 && typeof storage.free === 'number' ?
		Math.max(0, Math.min(100, (storage.size - storage.free) / storage.size * 100)) : null;
	var load = Array.isArray(info.load) && info.load.length >= 3 ? info.load.map(function(n) {
		return (n / 65535.0).toFixed(2);
	}) : null;
	return [
		card('运行时间', uptime(info.uptime), '自上次启动以来', 'clock'),
		card('CPU 负载', load ? load[0] : '暂不可用', load ? '1 / 5 / 15 分钟 · ' + load.join(' / ') : '等待设备响应', 'chip'),
		card('内存使用', memoryPercent == null ? '暂不可用' : memoryPercent.toFixed(0) + '%',
			memoryUsed == null ? '等待设备响应' : bytes(memoryUsed) + ' / ' + bytes(mem.total), 'memory', memoryPercent),
		card('系统存储', storagePercent == null ? '暂不可用' : storagePercent.toFixed(0) + '%',
			storage ? storage.mount + ' · ' + bytes(storage.avail) + ' 可用' : '等待挂载信息', 'disk', storagePercent)
	];
}

function groups(menu) {
	var definitions = [
		{ title: '移动网络', subtitle: '5G / 4G 拨号与模组管理', icon: 'signal', tone: 'teal', links: [
			['QModem 管理', ['admin/modem', 'admin/network/qmodem', 'admin/services/qmodem']]
		] },
		{ title: '网络代理', subtitle: '订阅、分流与连接状态', icon: 'globe', tone: 'blue', links: [
			['OpenClash', ['admin/openclash', 'admin/services/openclash']]
		] },
		{ title: '应用商店', subtitle: '发现与管理设备上的应用', icon: 'store', tone: 'orange', links: [
			['iStore 应用商店', ['admin/store']]
		] },
		{ title: '容器服务', subtitle: '容器、镜像与存储卷', icon: 'box', tone: 'blue', links: [
			['Docker 管理', ['admin/docker', 'admin/services/dockerman']]
		] },
		{ title: '网络存储', subtitle: '共享文件与磁盘管理', icon: 'folder', tone: 'orange', links: [
			['文件共享', ['admin/nas/samba4', 'admin/nas/samba', 'admin/services/samba4', 'admin/nas']],
			['挂载管理', ['admin/system/mounts', 'admin/system/fstab']],
			['FTP 服务', ['admin/nas/vsftpd', 'admin/services/vsftpd']]
		] },
		{ title: '网络与系统', subtitle: '接口、无线和日常维护', icon: 'network', tone: 'teal', links: [
			['网络接口', ['admin/network/network', 'admin/network']],
			['无线设置', ['admin/network/wireless']],
			['DDNS', ['admin/services/ddns', 'admin/services/ddns-go', 'admin/nas/ddns-go']],
			['系统设置', ['admin/system/system', 'admin/system']]
		] }
	];
	return definitions.map(function(group) {
		var links = group.links.map(function(item) { return link(menu, item[0], item[1]); }).filter(Boolean);
		return E('article', { 'class': 'm28c-app' }, [
			E('div', { 'class': 'm28c-app-heading' }, [
				E('span', { 'class': 'm28c-app-icon m28c-' + group.tone }, icon(group.icon)),
				E('div', {}, [E('h3', {}, group.title), E('p', {}, group.subtitle)])
			]),
			E('div', { 'class': 'm28c-app-links' }, links.length ? links : [
				E('span', { 'class': 'm28c-unavailable' }, '当前无可用入口')
			])
		]);
	});
}

function refreshData() {
	return Promise.all([
		L.resolveDefault(callInfo(), {}),
		L.resolveDefault(callMounts(), null)
	]);
}

return view.extend({
	load: function() {
		return Promise.all([
			L.resolveDefault(callBoard(), {}),
			refreshData(),
			L.resolveDefault(ui.menu.load(), {})
		]);
	},

	render: function(data) {
		var board = data[0], info = data[1][0], mounts = data[1][1], menu = data[2];
		var stats = E('div', { 'class': 'm28c-stats' }, renderStats(info, mounts));
		var status = E('span', { 'class': 'm28c-refresh', role: 'status' });
		var dataDisk = E('span', { 'class': 'm28c-data-disk' });
		function updateExtras(latestInfo, latestMounts) {
			var online = typeof latestInfo.uptime === 'number';
			status.classList.toggle('m28c-refresh-error', !online);
			status.textContent = online ? '已更新 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }) + ' · 每 10 秒刷新' : '状态暂不可用 · 正在重试';
			var opt = (latestMounts || []).filter(function(m) { return m.mount === '/opt'; })[0];
			dataDisk.textContent = !latestMounts ? '数据分区 /opt · 状态暂不可用' :
				opt ? '数据分区 /opt · ' + bytes(opt.avail) + ' 可用 / ' + bytes(opt.size) : '数据分区 /opt · 未挂载';
		}
		updateExtras(info, mounts);
		var setupLinks = [
			link(menu, '设置管理密码', ['admin/system/admin/password', 'admin/system/admin'], 'm28c-button m28c-button-primary'),
			link(menu, '配置 Wi-Fi', ['admin/network/wireless'], 'm28c-button')
		].filter(Boolean);
		var overview = link(menu, '完整系统概览', ['admin/status/overview'], 'm28c-section-link');
		var node = E('div', { 'class': 'm28c-dashboard' }, [
			E('link', { rel: 'stylesheet', href: L.resource('m28c/dashboard.css') }),
			E('section', { 'class': 'm28c-hero' }, [
				E('div', {}, [
					E('div', { 'class': 'm28c-eyebrow' }, 'MANGOPI / M28C'),
					E('h2', {}, '设备控制台'),
					E('p', {}, '连接、应用与存储，一屏掌握。'),
					E('div', { 'class': 'm28c-device' }, [
						E('span', {}, board.model || '设备信息暂不可用'),
						E('span', {}, board.hostname || '—')
					])
				]),
				E('div', { 'class': 'm28c-hero-mark', 'aria-hidden': 'true' }, [E('span', {}, 'M'), E('small', {}, '28C')])
			]),
			E('div', { 'class': 'm28c-section-heading' }, [E('h3', {}, '设备状态'), status]),
			stats,
			E('div', { 'class': 'm28c-system-strip' }, [
				E('span', {}, '内核 ' + (board.kernel || '—')),
				E('span', {}, board.release && board.release.description || '固件信息暂不可用'),
				dataDisk
			]),
			setupLinks.length ? E('section', { 'class': 'm28c-setup' }, [
				E('div', {}, [E('h3', {}, '首次使用，从这里开始'), E('p', {}, '设置管理密码，再配置 Wi-Fi 和上网方式。')]),
				E('div', { 'class': 'm28c-setup-actions' }, setupLinks)
			]) : null,
			E('div', { 'class': 'm28c-section-heading' }, [E('h3', {}, '常用功能'), overview]),
			E('div', { 'class': 'm28c-apps' }, groups(menu)),
			E('p', { 'class': 'm28c-footnote' }, '更多功能可从左侧菜单进入。状态数据来自当前设备。')
		]);
		poll.add(function() {
			return refreshData().then(function(latest) {
				while (stats.firstChild) stats.removeChild(stats.firstChild);
				renderStats(latest[0], latest[1]).forEach(function(el) { stats.appendChild(el); });
				updateExtras(latest[0], latest[1]);
			});
		}, 10);
		return node;
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
