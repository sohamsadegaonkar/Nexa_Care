Pod::Spec.new do |s|
  s.name           = 'NexaDeviceSecurity'
  s.version        = '1.0.0'
  s.summary        = 'Nexa Care non-exportable patient device signing keys'
  s.description    = 'Local Expo module for Secure Enclave patient signing keys.'
  s.author         = 'Nexa Care'
  s.homepage       = 'https://github.com/sohamsadegaonkar/Nexa_Care'
  s.license        = { :type => 'MIT' }
  s.platforms      = { :ios => '15.1' }
  s.source         = { :git => '' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'
  s.swift_version = '5.9'
  s.source_files = '**/*.{h,m,mm,swift,hpp,cpp}'
end
