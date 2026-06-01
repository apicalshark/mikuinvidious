package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/armon/go-socks5"
)

type Rotator struct {
	ipv4s    []net.IP
	ipv6s    []net.IP
	strategy string
	mu       sync.Mutex

	// State for Round-Robin
	idxV4 int
	idxV6 int

	// State for Periodic
	lastRotate  time.Time
	interval    time.Duration
	currentV4   net.IP
	currentV6   net.IP
}

func (r *Rotator) getIP(pool []net.IP, isIPv6 bool) net.IP {
	if len(pool) == 0 {
		return nil
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	switch r.strategy {
	case "round-robin":
		if isIPv6 {
			ip := pool[r.idxV6%len(pool)]
			r.idxV6++
			return ip
		} else {
			ip := pool[r.idxV4%len(pool)]
			r.idxV4++
			return ip
		}

	case "periodic":
		if time.Since(r.lastRotate) > r.interval || (isIPv6 && r.currentV6 == nil) || (!isIPv6 && r.currentV4 == nil) {
			r.lastRotate = time.Now()
			if len(r.ipv4s) > 0 {
				r.currentV4 = r.ipv4s[rand.Intn(len(r.ipv4s))]
			}
			if len(r.ipv6s) > 0 {
				r.currentV6 = r.ipv6s[rand.Intn(len(r.ipv6s))]
			}
			log.Printf("[Rotator] Periodic rotation triggered. New V4: %v, New V6: %v", r.currentV4, r.currentV6)
		}
		if isIPv6 {
			return r.currentV6
		}
		return r.currentV4

	case "random":
		fallthrough
	default:
		return pool[rand.Intn(len(pool))]
	}
}

func (r *Rotator) Dial(ctx context.Context, network, addr string) (net.Conn, error) {
	dialer := &net.Dialer{
		Timeout:   30 * time.Second,
		KeepAlive: 30 * time.Second,
	}

	// Determine if target is IPv6
	host, _, _ := net.SplitHostPort(addr)
	isIPv6 := false
	if ip := net.ParseIP(host); ip != nil {
		if ip.To4() == nil {
			isIPv6 = true
		}
	} else {
		// It's a hostname, we try to see if it resolves to IPv6 primarily if we have IPv6 pool
		// However, most reliable way is to let Dialer decide, but we need to pick a LocalAddr now.
		// As a heuristic: if we have IPv6s and it's a dual-stack target, we might want to prefer IPv6.
		// For simplicity, if we have both pools, we check if the target has AAAA records.
		ips, _ := net.LookupIP(host)
		for _, ip := range ips {
			if ip.To4() == nil {
				isIPv6 = true
				break
			}
		}
	}

	localIP := r.getIP(func() []net.IP {
		if isIPv6 {
			return r.ipv6s
		}
		return r.ipv4s
	}(), isIPv6)
	if localIP != nil {
		if strings.HasPrefix(network, "tcp") {
			dialer.LocalAddr = &net.TCPAddr{IP: localIP}
		} else if strings.HasPrefix(network, "udp") {
			dialer.LocalAddr = &net.UDPAddr{IP: localIP}
		}
		log.Printf("[Rotator] Outgoing (%s) via %s -> %s", network, localIP, addr)
	} else {
		log.Printf("[Rotator] Outgoing (%s) via default -> %s", network, addr)
	}

	return dialer.DialContext(ctx, network, addr)
}

func discoverIPs(ifaceName string) ([]net.IP, []net.IP, error) {
	iface, err := net.InterfaceByName(ifaceName)
	if err != nil {
		return nil, nil, err
	}

	addrs, err := iface.Addrs()
	if err != nil {
		return nil, nil, err
	}

	var v4s, v6s []net.IP
	for _, addr := range addrs {
		ipNet, ok := addr.(*net.IPNet)
		if !ok {
			continue
		}
		ip := ipNet.IP
		if ip.IsLoopback() || ip.IsLinkLocalUnicast() {
			continue
		}

		if ip.To4() != nil {
			v4s = append(v4s, ip)
		} else if ip.IsGlobalUnicast() {
			v6s = append(v6s, ip)
		}
	}
	return v4s, v6s, nil
}

func main() {
	rand.Seed(time.Now().UnixNano())

	port := os.Getenv("PORT")
	if port == "" {
		port = "1081"
	}

	var v4s, v6s []net.IP
	ifaceName := os.Getenv("BIND_INTERFACE")
	if ifaceName != "" {
		log.Printf("[Init] Discovering IPs on interface: %s", ifaceName)
		v4, v6, err := discoverIPs(ifaceName)
		if err != nil {
			log.Fatalf("[Error] IP discovery failed: %v", err)
		}
		v4s = append(v4s, v4...)
		v6s = append(v6s, v6...)
	}

	staticIPs := os.Getenv("BIND_IPS")
	if staticIPs != "" {
		for _, s := range strings.Split(staticIPs, ",") {
			ip := net.ParseIP(strings.TrimSpace(s))
			if ip != nil {
				if ip.To4() != nil {
					v4s = append(v4s, ip)
				} else {
					v6s = append(v6s, ip)
				}
			}
		}
	}

	log.Printf("[Init] Rotator initialized with %d IPv4s and %d IPv6s", len(v4s), len(v6s))
	for _, ip := range v4s {
		log.Printf("  - [v4] %s", ip)
	}
	for _, ip := range v6s {
		log.Printf("  - [v6] %s", ip)
	}

	strategy := strings.ToLower(os.Getenv("ROTATION_STRATEGY"))
	if strategy == "" {
		strategy = "random"
	}

	intervalStr := os.Getenv("ROTATION_INTERVAL")
	interval := 5 * time.Minute
	if d, err := time.ParseDuration(intervalStr); err == nil {
		interval = d
	}

	rotator := &Rotator{
		ipv4s:    v4s,
		ipv6s:    v6s,
		strategy: strategy,
		interval: interval,
	}
	log.Printf("[Init] Strategy: %s, Periodic Interval: %v", strategy, interval)
	conf := &socks5.Config{
		Dial: rotator.Dial,
	}

	server, err := socks5.New(conf)
	if err != nil {
		log.Fatalf("[Fatal] Failed to create SOCKS5 server: %v", err)
	}

	log.Printf("[Server] Miku-Proxy-Rotator listening on :%s", port)
	if err := server.ListenAndServe("tcp", ":"+port); err != nil {
		log.Fatalf("[Fatal] Server failed: %v", err)
	}
}
