# 文件作用：按 LuCI 订阅名称设置 Provider 别名，并同步缓存和界面订阅元数据。
# Loaded by OpenClash's custom overwrite hook. No subscriptions are fetched here.
require "yaml"
require "digest"
require "uci"

module ProviderAliases
  class SafeFailure < StandardError; end
  CACHE_ROOT = "/etc/openclash/proxy_provider"

  def self.single_address(value)
    parts = value.to_s.split(/[\r\n|]+/).map(&:strip).reject(&:empty?)
    return nil unless parts.length == 1
    item = parts.first
    return item if item.match?(/\Ahttps?:\/\//)
    match = item.match(/\A[^\r\n]+,(https?:\/\/[^\r\n]+)\z/)
    match && match[1]
  end

  def self.metadata
    # Disabled individual records can supply labels for an enabled aggregate.
    UCI.read_sections("/etc/config", "config_subscribe")
  end

  def self.label(value)
    name = value.to_s.strip
    return nil if name.empty? || name.bytesize > 240 || name.match?(/[\x00-\x1f\x7f]/)
    name
  end

  def self.safe_filename(name, url)
    base = name.gsub(/[\\\/:\x00-\x1f\x7f]/, "_").gsub(/\A[. ]+|[. ]+\z/, "_")
    base = base.each_char.take(55).join
    raise SafeFailure, "invalid filename" if base.empty? || base == "." || base == ".."
    base + "__" + Digest::SHA256.hexdigest(url)[0, 12] + ".yaml"
  end

  def self.cache_path(provider, name, root)
    path = provider["path"].to_s
    relative = if path.start_with?("./proxy_provider/")
                 path.delete_prefix("./proxy_provider/")
               elsif path.empty?
                 name
               else
                 File.basename(path)
               end
    full = File.expand_path(relative, root)
    raise SafeFailure, "cache outside root" unless full.start_with?(File.expand_path(root) + "/")
    full
  end

  def self.unknown_references(value, renamed, path = [], found = [])
    case value
    when Hash
      value.each do |key, child|
        next if path.empty? && key == "proxy-providers"
        next if path.length == 2 && path[0] == "proxy-groups" && key == "use"
        unknown_references(child, renamed, path + [key], found)
      end
    when Array
      value.each_with_index { |child, index| unknown_references(child, renamed, path + [index], found) }
    when String
      found << value if renamed.key?(value)
    end
    found.uniq
  end

  def self.plan(input, subscriptions, cache_root: CACHE_ROOT)
    raise SafeFailure, "configuration shape" unless input.is_a?(Hash)
    original = input["proxy-providers"]
    return [Marshal.load(Marshal.dump(input)), [], {"mapped" => 0, "missing" => 0, "ambiguous" => 0, "unknown_reference" => 0, "disambiguated" => 0}] if original.nil?
    raise SafeFailure, "provider shape" unless original.is_a?(Hash) && original.all? { |key, value| key.is_a?(String) && value.is_a?(Hash) }
    groups = input.fetch("proxy-groups", [])
    raise SafeFailure, "group shape" unless groups.is_a?(Array) && groups.all? { |g| g.is_a?(Hash) && (!g.key?("use") || g["use"].is_a?(Array)) }
    raise SafeFailure, "existing dangling reference" unless groups.all? { |g| g.fetch("use", []).all? { |name| original.key?(name) } }
    labels = {}
    subscriptions.each do |record|
      address = single_address(record["address"])
      name = label(record["name"])
      next unless address && name
      (labels[address] ||= []) << name
    end
    labels.transform_values!(&:uniq)
    stats = {"mapped" => 0, "missing" => 0, "ambiguous" => 0, "unknown_reference" => 0, "disambiguated" => 0}
    aliases = {}
    original.each do |old_name, provider|
      next unless provider["type"] == "http" && provider["url"].is_a?(String)
      choices = labels.fetch(provider["url"], [])
      if choices.empty?
        stats["missing"] += 1
      elsif choices.length > 1
        stats["ambiguous"] += 1
      else
        aliases[old_name] = choices.first
      end
    end
    duplicate_labels = aliases.values.tally.select { |_, count| count > 1 }.keys
    aliases.each do |old_name, new_name|
      if duplicate_labels.include?(new_name)
        aliases[old_name] = new_name + "__" + Digest::SHA256.hexdigest(original[old_name].fetch("url"))[0, 12]
        stats["disambiguated"] += 1
      end
    end
    unknown = unknown_references(input, aliases.reject { |old, new_name| old == new_name })
    unknown.each { |old| aliases.delete(old) }
    stats["unknown_reference"] = unknown.length
    # Resolve collisions conservatively, including names retained for unmapped entries.
    loop do
      desired = original.keys.to_h { |old| [old, aliases.fetch(old, old)] }
      collision_names = desired.values.tally.select { |_, count| count > 1 }.keys
      break if collision_names.empty?
      removed = aliases.keys.select { |old| collision_names.include?(desired[old]) }
      raise SafeFailure, "name collision" if removed.empty?
      removed.each { |old| aliases.delete(old) }
      stats["ambiguous"] += removed.length
    end
    result = Marshal.load(Marshal.dump(input))
    renamed = {}
    migrations = []
    original.each do |old_name, provider|
      new_name = aliases.fetch(old_name, old_name)
      new_provider = Marshal.load(Marshal.dump(provider))
      if aliases.key?(old_name)
        filename = safe_filename(new_name, provider.fetch("url"))
        destination = File.expand_path(filename, cache_root)
        source = cache_path(provider, old_name, cache_root)
        new_provider["path"] = "./proxy_provider/" + filename
        migrations << {"source" => source, "destination" => destination} unless source == destination
      end
      renamed[new_name] = new_provider
    end
    result["proxy-providers"] = renamed
    result.fetch("proxy-groups", []).each { |g| g["use"] = g["use"].map { |old| aliases.fetch(old, old) } if g.key?("use") }
    raise SafeFailure, "result dangling reference" unless result.fetch("proxy-groups", []).all? { |g| g.fetch("use", []).all? { |name| renamed.key?(name) } }
    stats["mapped"] = aliases.length
    [result, migrations, stats]
  end

  def self.regular_cache?(path)
    return false unless File.exist?(path) || File.symlink?(path)
    stat = File.lstat(path)
    raise SafeFailure, "unsafe cache type" unless stat.file? && !stat.symlink? && stat.uid == Process.uid
    true
  end

  # LuCI's overview reads subscribe_info before looking at source YAML. Use its
  # native URL#name=label format; provider URLs and credentials never leave RAM.
  def self.info_plan(text, config_name, providers)
    desired = providers.filter_map do |name, provider|
      next unless provider["type"] == "http" && provider["url"].is_a?(String)
      url = provider["url"]
      raise SafeFailure, "unsafe metadata" unless url.match?(/\Ahttps?:\/\//) && !url.match?(/[\r\n\x00]/) && !url.include?("#name=") && label(name)
      url + "#name=" + name
    end
    return [text, {"info_entries" => 0, "info_changed" => false}] if desired.empty?
    sections = UCI.sections(text)
    target = sections.find { |s| s[:type] == "subscribe_info" && UCI.options(s)["name"] == config_name }
    if target
      options = UCI.options(target)
      old = options["url"]
      old = old.to_s.split(/\n/).reject(&:empty?) unless old.is_a?(Array)
      return [text, {"info_entries" => desired.length, "info_changed" => false}] if old == desired
      lines = target[:lines].reject { |line| line.match?(/\A\s*(?:option|list)\s+url\s/) }
      lines << "\n" unless lines.last.to_s.end_with?("\n")
      desired.each { |value| lines << "\tlist url " + UCI.quote(value) + "\n" }
      target[:lines] = lines
    else
      identifier = "codex_alias_" + Digest::SHA256.hexdigest(config_name)[0, 16]
      raise SafeFailure, "metadata section collision" if text.match?(/^config\s+\S+\s+['"]?#{Regexp.escape(identifier)}['"]?\s*$/)
      sections << {type: "subscribe_info", lines: ["\nconfig subscribe_info " + UCI.quote(identifier) + "\n", "\toption name " + UCI.quote(config_name) + "\n"] + desired.map { |value| "\tlist url " + UCI.quote(value) + "\n" }}
    end
    [sections.map { |s| s[:lines].join }.join, {"info_entries" => desired.length, "info_changed" => true}]
  end

  def self.sync_info(path, package_path: "/etc/config/openclash", pending_path: "/tmp/.uci/openclash")
    # Never commit another UI session's pending settings. Defer just the overview
    # metadata if pending changes exist; the runtime aliases still take effect.
    return {"info_changed" => false, "info_deferred" => true} if File.file?(pending_path) && File.size(pending_path) > 0
    stat = File.lstat(package_path)
    raise SafeFailure, "unsafe metadata file" unless stat.file? && !stat.symlink? && stat.uid == Process.uid
    before = File.read(package_path, encoding: "UTF-8")
    config = YAML.load_file(path)
    config_name = File.basename(path).sub(/\.ya?ml\z/i, "")
    result, stats = info_plan(before, config_name, config.fetch("proxy-providers", {}))
    return stats unless stats["info_changed"]
    directory = File.join(File.dirname(package_path), ".provider-alias-check-" + Process.pid.to_s)
    Dir.mkdir(directory, 0700)
    begin
      File.write(File.join(directory, "openclash"), result, mode: "w", perm: 0600)
      # The installed uci parser must accept the exact candidate, including names
      # with quotes or backslashes. No import, save or commit is performed.
      parsed = IO.popen(["uci", "-c", directory, "-q", "export", "openclash"], err: File::NULL, &:read)
      raise SafeFailure, "metadata parser rejected candidate" unless $?.success? && !parsed.empty?
      actual = UCI.sections(parsed).find { |s| s[:type] == "subscribe_info" && UCI.options(s)["name"] == config_name }
      intended = UCI.sections(result).find { |s| s[:type] == "subscribe_info" && UCI.options(s)["name"] == config_name }
      raise SafeFailure, "metadata round trip differs" unless actual && UCI.options(actual) == UCI.options(intended)
    ensure
      File.unlink(File.join(directory, "openclash")) if File.file?(File.join(directory, "openclash"))
      Dir.rmdir(directory)
    end
    raise SafeFailure, "metadata changed concurrently" unless File.read(package_path, encoding: "UTF-8") == before && !(File.file?(pending_path) && File.size(pending_path) > 0)
    temporary = package_path + ".aliases-" + Process.pid.to_s
    begin
      File.open(temporary, File::WRONLY | File::CREAT | File::EXCL, stat.mode & 0777) { |file| file.write(result); file.flush; file.fsync }
      File.rename(temporary, package_path)
    ensure
      File.unlink(temporary) if File.file?(temporary) && !File.symlink?(temporary)
    end
    stats
  end

  def self.copy_cache(source, destination)
    return "existing" if regular_cache?(destination)
    return "missing" unless regular_cache?(source) && File.size(source) > 0
    temporary = destination + ".alias-" + Process.pid.to_s
    begin
      File.open(temporary, File::WRONLY | File::CREAT | File::EXCL, 0600) do |file|
        File.open(source, "rb") { |old| IO.copy_stream(old, file) }
        file.flush
        file.fsync
      end
      raise SafeFailure, "cache copy mismatch" unless Digest::SHA256.file(source).hexdigest == Digest::SHA256.file(temporary).hexdigest
      begin
        File.link(temporary, destination) # Atomic create; a current destination is never overwritten.
        "copied"
      rescue Errno::EEXIST
        regular_cache?(destination)
        "existing"
      end
    ensure
      File.unlink(temporary) if File.file?(temporary) && !File.symlink?(temporary)
    end
  end

  def self.apply(path, subscriptions: nil, cache_root: CACHE_ROOT)
    stat = File.lstat(path)
    raise SafeFailure, "unsafe configuration file" unless stat.file? && !stat.symlink? && stat.uid == Process.uid
    before = File.binread(path)
    config = YAML.load(before)
    result, migrations, stats = plan(config, subscriptions || metadata, cache_root: cache_root)
    stats.merge!({"cache_copied" => 0, "cache_existing" => 0, "cache_missing" => 0})
    if result == config
      stats["changed"] = false
      return stats
    end
    migrations.each do |migration|
      status = copy_cache(migration.fetch("source"), migration.fetch("destination"))
      stats["cache_" + status] += 1
    end
    raise SafeFailure, "configuration changed during processing" unless File.binread(path) == before
    temporary = path + ".aliases-" + Process.pid.to_s
    begin
      File.open(temporary, File::WRONLY | File::CREAT | File::EXCL, stat.mode & 0777) do |file|
        file.write(YAML.original_dump(result))
        file.flush
        file.fsync
      end
      raise SafeFailure, "serialized object differs" unless YAML.load_file(temporary) == result
      File.rename(temporary, path)
    ensure
      File.unlink(temporary) if File.file?(temporary) && !File.symlink?(temporary)
    end
    stats["changed"] = true
    stats
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    raise ProviderAliases::SafeFailure, "one configuration argument required" unless ARGV.length == 1
    stats = ProviderAliases.apply(ARGV.fetch(0))
    begin
      stats.merge!(ProviderAliases.sync_info(ARGV.fetch(0)))
    rescue Exception => error
      # Runtime aliasing may already have succeeded; report that distinction.
      stats["info_failed_class"] = error.class.to_s
    end
    puts "Provider aliases: " + stats.map { |key, value| key + "=" + value.to_s }.join(" ")
  rescue Exception => error
    # Exception messages and data may contain subscription credentials. Never print them.
    puts "Provider aliases: failed class=" + error.class.to_s + "; original configuration retained"
    exit 1
  end
end
