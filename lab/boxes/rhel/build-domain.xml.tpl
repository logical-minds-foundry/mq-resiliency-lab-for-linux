<!-- Transient RHEL base-box build domain - host-resolved recipe (#327): @DOMAIN_TYPE@/@CPU_MODE@
     are substituted by build-box.sh from the args mqlab passes (KVM on a native-x86
     host; TCG — the #24-proven x86 recipe — on the arm64 Mac).
     @ISO@/@DOMAIN@/@DISK@/@CONSOLE@ are substituted by build-box.sh (per RHEL major). Boots DVD + OEMDRV kickstart,
     installs unattended, powers off (ks 'poweroff'), on_reboot=destroy
     keeps anaconda's mid-install reboot from looping the installer. -->
<domain type='@DOMAIN_TYPE@'>
  <name>@DOMAIN@</name>
  <memory unit='MiB'>4096</memory>
  <vcpu>4</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='cdrom'/>
    <boot dev='hd'/>
  </os>
  <features><acpi/></features>
  <cpu mode='@CPU_MODE@'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>destroy</on_reboot>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='@DISK@'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='@ISO@'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/var/lib/libvirt/images/oemdrv.iso'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='vagrant-libvirt'/>
      <model type='virtio'/>
    </interface>
    <serial type='file'>
      <source path='@CONSOLE@'/>
      <target port='0'/>
    </serial>
  </devices>
</domain>
