import ExpoModulesCore
import Foundation
import Security

public final class NexaDeviceSecurityModule: Module {
  private let keyTagPrefix = "ai.nexacare.patient.signing."

  public func definition() -> ModuleDefinition {
    Name("NexaDeviceSecurity")

    AsyncFunction("generateKey") { (alias: String) -> [String: Any] in
      let tag = try self.applicationTag(alias)
      if let existing = try self.loadPrivateKey(tag: tag) {
        return try self.describeKey(existing, alias: alias)
      }

      var accessError: Unmanaged<CFError>?
      guard let accessControl = SecAccessControlCreateWithFlags(
        nil,
        kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        [.privateKeyUsage],
        &accessError
      ) else {
        throw accessError?.takeRetainedValue() ?? self.error("KEY_ACCESS_CONTROL_FAILED")
      }

      let attributes: [String: Any] = [
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrKeySizeInBits as String: 256,
        kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
        kSecPrivateKeyAttrs as String: [
          kSecAttrIsPermanent as String: true,
          kSecAttrApplicationTag as String: tag,
          kSecAttrAccessControl as String: accessControl,
        ],
      ]

      var createError: Unmanaged<CFError>?
      guard let privateKey = SecKeyCreateRandomKey(attributes as CFDictionary, &createError) else {
        throw createError?.takeRetainedValue() ?? self.error("SECURE_ENCLAVE_KEY_GENERATION_FAILED")
      }
      return try self.describeKey(privateKey, alias: alias)
    }

    AsyncFunction("getKey") { (alias: String) -> [String: Any]? in
      let tag = try self.applicationTag(alias)
      guard let privateKey = try self.loadPrivateKey(tag: tag) else { return nil }
      return try self.describeKey(privateKey, alias: alias)
    }

    AsyncFunction("sign") { (alias: String, message: String) -> String in
      let tag = try self.applicationTag(alias)
      guard let privateKey = try self.loadPrivateKey(tag: tag) else {
        throw self.error("DEVICE_KEY_NOT_FOUND")
      }
      guard self.isSecureEnclaveKey(privateKey) else {
        throw self.error("DEVICE_KEY_NOT_SECURE_ENCLAVE")
      }
      guard SecKeyIsAlgorithmSupported(privateKey, .sign, .ecdsaSignatureMessageX962SHA256) else {
        throw self.error("DEVICE_KEY_SIGNING_UNSUPPORTED")
      }

      var signError: Unmanaged<CFError>?
      guard let signature = SecKeyCreateSignature(
        privateKey,
        .ecdsaSignatureMessageX962SHA256,
        Data(message.utf8) as CFData,
        &signError
      ) as Data? else {
        throw signError?.takeRetainedValue() ?? self.error("DEVICE_KEY_SIGNING_FAILED")
      }
      return signature.base64EncodedString()
    }

    AsyncFunction("deleteKey") { (alias: String) -> Bool in
      let tag = try self.applicationTag(alias)
      let query: [String: Any] = [
        kSecClass as String: kSecClassKey,
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrApplicationTag as String: tag,
      ]
      let status = SecItemDelete(query as CFDictionary)
      if status == errSecSuccess { return true }
      if status == errSecItemNotFound { return false }
      throw self.osStatusError(status, code: "DEVICE_KEY_DELETE_FAILED")
    }
  }

  private func applicationTag(_ alias: String) throws -> Data {
    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    guard !alias.isEmpty, alias.count <= 96, alias.unicodeScalars.allSatisfy({ allowed.contains($0) }) else {
      throw error("DEVICE_KEY_ALIAS_INVALID")
    }
    return Data((keyTagPrefix + alias).utf8)
  }

  private func loadPrivateKey(tag: Data) throws -> SecKey? {
    let query: [String: Any] = [
      kSecClass as String: kSecClassKey,
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
      kSecAttrApplicationTag as String: tag,
      kSecReturnRef as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess, let key = item as! SecKey? else {
      throw osStatusError(status, code: "DEVICE_KEY_LOOKUP_FAILED")
    }
    return key
  }

  private func describeKey(_ privateKey: SecKey, alias: String) throws -> [String: Any] {
    guard isSecureEnclaveKey(privateKey) else {
      throw error("DEVICE_KEY_NOT_SECURE_ENCLAVE")
    }
    guard let publicKey = SecKeyCopyPublicKey(privateKey) else {
      throw error("DEVICE_PUBLIC_KEY_UNAVAILABLE")
    }
    var exportError: Unmanaged<CFError>?
    guard let x963 = SecKeyCopyExternalRepresentation(publicKey, &exportError) as Data? else {
      throw exportError?.takeRetainedValue() ?? error("DEVICE_PUBLIC_KEY_EXPORT_FAILED")
    }
    guard x963.count == 65, x963.first == 0x04 else {
      throw error("DEVICE_PUBLIC_KEY_FORMAT_INVALID")
    }

    // ASN.1 SubjectPublicKeyInfo prefix for id-ecPublicKey + prime256v1,
    // followed by an uncompressed 65-byte X9.63 point.
    var spki = Data([
      0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01,
      0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ])
    spki.append(x963)

    return [
      "alias": alias,
      "publicKeyDerBase64": spki.base64EncodedString(),
      "platform": "ios",
      "custody": "ios-secure-enclave",
      "nonExportable": true,
      "hardwareBacked": true,
      "strongBoxBacked": false,
    ]
  }

  private func isSecureEnclaveKey(_ key: SecKey) -> Bool {
    guard let attributes = SecKeyCopyAttributes(key) as? [CFString: Any] else { return false }
    guard let token = attributes[kSecAttrTokenID] else { return false }
    return String(describing: token) == String(describing: kSecAttrTokenIDSecureEnclave)
  }

  private func error(_ code: String) -> NSError {
    NSError(domain: "NexaDeviceSecurity", code: 1, userInfo: [NSLocalizedDescriptionKey: code])
  }

  private func osStatusError(_ status: OSStatus, code: String) -> NSError {
    let detail = SecCopyErrorMessageString(status, nil) as String? ?? "OSStatus \(status)"
    return NSError(
      domain: "NexaDeviceSecurity",
      code: Int(status),
      userInfo: [NSLocalizedDescriptionKey: "\(code): \(detail)"]
    )
  }
}
