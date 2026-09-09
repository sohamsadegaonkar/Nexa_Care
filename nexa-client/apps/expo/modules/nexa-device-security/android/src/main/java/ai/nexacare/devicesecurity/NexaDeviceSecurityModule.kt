package ai.nexacare.devicesecurity

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyInfo
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.util.Base64
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.ProviderException
import java.security.Signature
import java.security.spec.ECGenParameterSpec

class NexaDeviceSecurityModule : Module() {
  private val keyStoreName = "AndroidKeyStore"

  override fun definition() = ModuleDefinition {
    Name("NexaDeviceSecurity")

    AsyncFunction("generateKey") { alias: String ->
      requireAlias(alias)
      val keyStore = loadKeyStore()
      if (!keyStore.containsAlias(alias)) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
          try {
            generate(alias, strongBox = true)
          } catch (_: StrongBoxUnavailableException) {
            generate(alias, strongBox = false)
          } catch (_: ProviderException) {
            // Devices may advertise StrongBox but reject this key configuration.
            generate(alias, strongBox = false)
          }
        } else {
          generate(alias, strongBox = false)
        }
      }
      describe(alias)
    }

    AsyncFunction("getKey") { alias: String ->
      requireAlias(alias)
      val keyStore = loadKeyStore()
      if (!keyStore.containsAlias(alias)) null else describe(alias)
    }

    AsyncFunction("sign") { alias: String, message: String ->
      requireAlias(alias)
      val privateKey = privateKey(alias)
      if (privateKey.encoded != null) {
        throw IllegalStateException("DEVICE_KEY_EXPORTABLE")
      }
      val signer = Signature.getInstance("SHA256withECDSA")
      signer.initSign(privateKey)
      signer.update(message.toByteArray(Charsets.UTF_8))
      Base64.encodeToString(signer.sign(), Base64.NO_WRAP)
    }

    AsyncFunction("deleteKey") { alias: String ->
      requireAlias(alias)
      val keyStore = loadKeyStore()
      val existed = keyStore.containsAlias(alias)
      if (existed) keyStore.deleteEntry(alias)
      existed
    }
  }

  private fun loadKeyStore(): KeyStore = KeyStore.getInstance(keyStoreName).apply { load(null) }

  private fun requireAlias(alias: String) {
    require(alias.isNotBlank() && alias.length <= 96 && alias.matches(Regex("[A-Za-z0-9._-]+"))) {
      "DEVICE_KEY_ALIAS_INVALID"
    }
  }

  private fun generate(alias: String, strongBox: Boolean) {
    val builder = KeyGenParameterSpec.Builder(
      alias,
      KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY
    )
      .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
      .setDigests(KeyProperties.DIGEST_SHA256)
      .setUserAuthenticationRequired(false)

    if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
      builder.setIsStrongBoxBacked(true)
    }

    val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, keyStoreName)
    generator.initialize(builder.build())
    generator.generateKeyPair()
  }

  private fun privateKey(alias: String): PrivateKey {
    val entry = loadKeyStore().getEntry(alias, null) as? KeyStore.PrivateKeyEntry
      ?: throw IllegalStateException("DEVICE_KEY_NOT_FOUND")
    return entry.privateKey
  }

  private fun describe(alias: String): Map<String, Any> {
    val entry = loadKeyStore().getEntry(alias, null) as? KeyStore.PrivateKeyEntry
      ?: throw IllegalStateException("DEVICE_KEY_NOT_FOUND")
    val privateKey = entry.privateKey
    if (privateKey.encoded != null) {
      throw IllegalStateException("DEVICE_KEY_EXPORTABLE")
    }
    val keyInfo = KeyFactory.getInstance(privateKey.algorithm, keyStoreName)
      .getKeySpec(privateKey, KeyInfo::class.java)
    val hardwareBacked = keyInfo.isInsideSecureHardware
    val strongBoxBacked = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
      keyInfo.securityLevel == KeyProperties.SECURITY_LEVEL_STRONGBOX
    } else {
      false
    }
    val custody = when {
      strongBoxBacked -> "android-strongbox"
      hardwareBacked -> "android-keystore-hardware"
      else -> "android-keystore"
    }
    return mapOf(
      "alias" to alias,
      "publicKeyDerBase64" to Base64.encodeToString(entry.certificate.publicKey.encoded, Base64.NO_WRAP),
      "platform" to "android",
      "custody" to custody,
      "nonExportable" to true,
      "hardwareBacked" to hardwareBacked,
      "strongBoxBacked" to strongBoxBacked,
    )
  }
}
