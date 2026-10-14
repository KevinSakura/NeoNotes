using System.Collections.Concurrent;
using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using OpenUtau.Core;
using OpenUtau.Core.DiffSinger;
using OpenUtau.Core.Render;
using OpenUtau.Core.Ustx;
using OpenUtau.Core.Util;
using Serilog;
using UstxFormat = OpenUtau.Core.Format.Ustx;

// This is NeoNote's host protocol, not a command supplied by OpenUtau.
// No desktop window, playback device, model download or network request is used.
internal static class Program {
    static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    static int Main(string[] args) {
        try {
            System.Text.Encoding.RegisterProvider(System.Text.CodePagesEncodingProvider.Instance);
            if (args.Length == 1 && args[0] == "--probe") {
                Console.WriteLine(JsonSerializer.Serialize(new {
                    protocol = "neonote-vocal-v1", openutau = "0.1.573-beta",
                    devices = Onnx.getGpuInfo().Select(g => new { id = g.deviceId, name = g.description }),
                    onnx = Assembly.Load("Microsoft.ML.OnnxRuntime").GetName().Version?.ToString()
                }));
                return 0;
            }
            if (args.Length == 2 && args[0] == "--validate-request") {
                using var document = JsonDocument.Parse(File.ReadAllText(args[1]));
                var mapping = ValidateInput(document.RootElement);
                Console.WriteLine(JsonSerializer.Serialize(new {
                    protocol = document.RootElement.GetProperty("protocol").GetString(),
                    validation_only = true, inference_count = 0,
                    parts = mapping.Select(n => n.part_index).Distinct().Count(), notes = mapping,
                    lyrics = mapping.Select(n => n.extension_index == 0 ? "word_onset" : "+").ToArray()
                }, JsonOptions));
                return 0;
            }
            if (args.Length != 2 || args[0] != "--request") throw new ArgumentException("Use --request <JSON> or --probe.");
            Render(Path.GetFullPath(args[1]));
            return 0;
        } catch (Exception e) {
            Console.Error.WriteLine(e.ToString());
            return 1;
        }
    }

    internal sealed record SourceNote(string note_id, int? uid, int line_index, int unit_index,
        int part_index, string phrase_id, int position, int duration, int tone, int extension_index);

    static JsonElement InputParts(JsonElement input) {
        return input.GetProperty(input.GetProperty("protocol").GetString() == "neonote-vocal-v2" ? "phrases" : "lines");
    }

    static List<SourceNote> ValidateInput(JsonElement input) {
        var protocol = input.GetProperty("protocol").GetString();
        if (protocol != "neonote-vocal-v1" && protocol != "neonote-vocal-v2") throw new InvalidDataException("Unsupported protocol.");
        if (input.TryGetProperty("singing_policy", out var policy)
            && (policy.GetProperty("pitch").GetString() != "score" || policy.GetProperty("vibrato").GetInt32() != 0
                || policy.GetProperty("merge_nearby_phrases").GetBoolean())) throw new InvalidDataException("Unsupported singing policy.");
        var result = new List<SourceNote>();
        var ids = new HashSet<string>();
        var uids = new List<int>();
        int partIndex = 0, previousEnd = -1;
        foreach (var part in InputParts(input).EnumerateArray()) {
            int line = part.GetProperty("line_index").GetInt32();
            var phrase = protocol == "neonote-vocal-v2" ? part.GetProperty("phrase_id").GetString()! : "line-" + line;
            foreach (var unit in part.GetProperty("units").EnumerateArray()) {
                int? uid = protocol == "neonote-vocal-v2" ? unit.GetProperty("uid").GetInt32() : null;
                if (uid.HasValue) uids.Add(uid.Value);
                int extension = 0, groupEnd = -1;
                foreach (var note in unit.GetProperty("notes").EnumerateArray()) {
                    var id = note.GetProperty("note_id").GetString()!;
                    int tick = note.GetProperty("position").GetInt32(), duration = note.GetProperty("duration").GetInt32();
                    int tone = note.GetProperty("tone").GetInt32();
                    if (!ids.Add(id) || tick < previousEnd || duration <= 0 || tone < 48 || tone > 84
                        || tick + duration > input.GetProperty("end_tick").GetInt32()
                        || (extension > 0 && tick != groupEnd)) throw new InvalidDataException("Invalid/fragmented source note group.");
                    result.Add(new SourceNote(id, uid, line, unit.GetProperty("unit_index").GetInt32(),
                        partIndex, phrase, tick, duration, tone, extension++));
                    previousEnd = groupEnd = tick + duration;
                }
                if (extension == 0) throw new InvalidDataException("Empty note group.");
            }
            partIndex++;
        }
        if (protocol == "neonote-vocal-v2" && !uids.SequenceEqual(Enumerable.Range(0,
            input.GetProperty("lyrics_identity").GetProperty("units").GetArrayLength())))
            throw new InvalidDataException("Incomplete or reordered source uid map.");
        if (result.Count == 0) throw new InvalidDataException("Empty vocal request.");
        return result;
    }

    static void SetPath(string name, string value) => typeof(PathManager).GetProperty(name)!.SetValue(PathManager.Inst, value);
    static void Render(string requestFile) {
        var clock = Stopwatch.StartNew();
        using var doc = JsonDocument.Parse(File.ReadAllText(requestFile));
        var input = doc.RootElement;
        var sourceNotes = ValidateInput(input);
        var resourceRoot = Path.GetFullPath(input.GetProperty("resource_root").GetString()!);
        var output = Path.GetDirectoryName(requestFile)!;
        SetPath("DataPath", resourceRoot);
        SetPath("CachePath", Path.Combine(output, "cache"));
        Directory.CreateDirectory(PathManager.Inst.CachePath);
        Log.Logger = new LoggerConfiguration().MinimumLevel.Information().WriteTo.Console(standardErrorFromLevel: Serilog.Events.LogEventLevel.Verbose).CreateLogger();
        var prefs = Preferences.Default;
        prefs.OnnxRunner = input.GetProperty("runner").GetString()!;
        prefs.OnnxGpu = input.GetProperty("gpu").GetInt32();
        prefs.DiffSingerSteps = 20;
        prefs.DiffSingerStepsVariance = 20;
        prefs.DiffSingerStepsPitch = 10;
        prefs.DiffSingerDepth = 0.6;
        prefs.DiffSingerTensorCache = false;
        prefs.DiffSingerMergeNearbyPhrases = false;
        using var scheduler = new PumpScheduler();
        DocManager.Inst.PostOnUIThread = scheduler.Post;
        DocManager.Inst.Initialize(Thread.CurrentThread, scheduler);
        SingerManager.Inst.Initialize();
        var singer = SingerManager.Inst.Singers.Values.Single(s => s.SingerType == USingerType.DiffSinger && string.Equals(s.Location, Path.Combine(resourceRoot, "singers", "Qixuan_v2.7.0_DiffSinger_OpenUtau"), StringComparison.OrdinalIgnoreCase));
        singer.EnsureLoaded();
        if (!singer.Loaded || singer.Errors.Count > 0) throw new InvalidDataException("Singer loading failed: " + string.Join("; ", singer.Errors));
        var project = UstxFormat.Create();
        project.name = "NeoNote / 绮萱 / DiffSinger (AI合成，非商业演示)";
        project.ustxVersion = UstxFormat.kUstxVersion;
        project.FilePath = Path.Combine(output, "singing.ustx");
        project.tempos = input.GetProperty("tempos").EnumerateArray().Select(t => new UTempo(t.GetProperty("position").GetInt32(), t.GetProperty("bpm").GetDouble())).ToList();
        project.timeSignatures = input.GetProperty("time_signatures").EnumerateArray().Select(t => new UTimeSignature(t.GetProperty("bar_position").GetInt32(), t.GetProperty("numerator").GetInt32(), t.GetProperty("denominator").GetInt32())).ToList();
        var track = project.tracks[0];
        track.TrackName = "绮萱 / DiffSinger";
        track.Singer = singer;
        track.Phonemizer = new DiffSingerChinesePhonemizer();
        track.RendererSettings = new URenderSettings { renderer = "DIFFSINGER", Renderer = new DiffSingerRenderer() };
        foreach (var line in InputParts(input).EnumerateArray()) {
            var part = new UVoicePart { name = input.GetProperty("protocol").GetString() == "neonote-vocal-v2"
                ? "乐句 " + line.GetProperty("phrase_id").GetString() + " / 原歌词第" + (line.GetProperty("line_index").GetInt32() + 1) + "行"
                : "歌词第" + (line.GetProperty("line_index").GetInt32() + 1) + "行",
                trackNo = 0, position = 0, duration = input.GetProperty("end_tick").GetInt32() };
            foreach (var unit in line.GetProperty("units").EnumerateArray()) {
                var index = 0;
                foreach (var src in unit.GetProperty("notes").EnumerateArray()) {
                    var note = project.CreateNote(src.GetProperty("tone").GetInt32(), src.GetProperty("position").GetInt32(), src.GetProperty("duration").GetInt32());
                    note.lyric = index++ == 0 ? unit.GetProperty("lyric").GetString()! : "+";
                    note.vibrato.length = 0;
                    part.notes.Add(note);
                }
            }
            project.parts.Add(part);
        }
        DocManager.Inst.ExecuteCmd(new LoadProjectNotification(project));
        project.ValidateFull();
        scheduler.Until(() => project.parts.OfType<UVoicePart>().All(p => p.PhonemesUpToDate), TimeSpan.FromMinutes(5));
        foreach (var part in project.parts.OfType<UVoicePart>()) {
            if (part.notes.Any(n => n.Error) || part.phonemes.Count == 0 || part.phonemes.Any(p => p.Error)) throw new InvalidDataException("Phonemizer rejected " + part.name);
        }
        scheduler.Until(() => project.parts.OfType<UVoicePart>().All(p => p.renderPhrases.Count > 0), TimeSpan.FromMinutes(2));
        UstxFormat.Save(project.FilePath, project);
        var phrases = project.parts.OfType<UVoicePart>().SelectMany((p, i) =>
            p.renderPhrases.Select(phrase => new { part_index = i, phrase })).OrderBy(p => p.phrase.positionMs).ToArray();
        var frames = input.GetProperty("frames").GetInt32();
        var samples = new float[frames];
        var results = new List<object>();
        var progress = new Progress(phrases.Sum(p => p.phrase.phones.Length));
        using var cancel = new CancellationTokenSource();
        for (var i = 0; i < phrases.Length; ++i) {
            Console.WriteLine(JsonSerializer.Serialize(new { phase = "生成演唱", completed = i, total = phrases.Length }));
            var phrase = phrases[i].phrase;
            var task = new DiffSingerRenderer().Render(phrase, progress, 0, cancel, true);
            scheduler.Until(() => task.IsCompleted, TimeSpan.FromMinutes(10));
            var result = task.GetAwaiter().GetResult();
            if (result.samples == null || result.samples.Length == 0 || result.samples.Any(v => !float.IsFinite(v))) throw new InvalidDataException("Invalid vocal samples.");
            // OpenUtau explicitly reports leading model padding in milliseconds.
            // Place each result absolutely; no concatenation or silence-based trimming.
            var first = (int)Math.Round((result.positionMs - result.leadingMs) * 44.1);
            if (first < 0) throw new InvalidDataException("Initial note needs leading silence for consonants/padding; edit the score.");
            if (first + result.samples.Length > frames) throw new InvalidDataException("Vocal tail exceeds the saved timeline plus three seconds.");
            for (var j = 0; j < result.samples.Length; ++j) samples[first + j] += result.samples[j];
            WriteFloatWav(Path.Combine(output, $"phrase-{i:D4}.wav"), result.samples);
            var partSources = sourceNotes.Where(n => n.part_index == phrases[i].part_index).ToArray();
            results.Add(new {
                index = i, part_index = phrases[i].part_index,
                position_ms = result.positionMs, leading_ms = result.leadingMs,
                first_frame = first, frames = result.samples.Length,
                phonemes = phrase.phones.Select(p => p.phoneme).ToArray(),
                notes = phrase.notes.Select(n => new { n.lyric, n.tone, n.positionMs, n.durationMs,
                    source = partSources.FirstOrDefault(s => s.position == phrase.position + n.position
                        && s.duration == n.duration && s.tone == n.tone) }).ToArray(),
                phoneme_timing = phrase.phones.Select((p, phoneIndex) => {
                    var src = p.noteIndex >= 0 && p.noteIndex < partSources.Length ? partSources[p.noteIndex] : null;
                    return new {
                        index = phoneIndex, p.phoneme, part_note_index = p.noteIndex,
                        start_ms = p.positionMs, end_ms = p.endMs, leading_ms = p.leadingMs,
                        source = src,
                        relative_to_source_note_ms = src == null ? (double?)null
                            : p.positionMs - phrase.timeAxis.TickPosToMsPos(src.position),
                        mapping = "OpenUtau source-part noteIndex; negative offset starts before the note"
                    };
                }).ToArray()
            });
        }
        if (!samples.Any(v => Math.Abs(v) > 0.00001)) throw new InvalidDataException("Silent vocal output.");
        WriteFloatWav(Path.Combine(output, "vocal-raw.wav"), samples);
        File.WriteAllText(Path.Combine(output, "worker-report.json"), JsonSerializer.Serialize(new {
            protocol = input.GetProperty("protocol").GetString(), openutau = "0.1.573-beta", runner = prefs.OnnxRunner, gpu = prefs.OnnxGpu,
            singer = singer.Name, sample_rate = 44100, frames, elapsed_seconds = clock.Elapsed.TotalSeconds,
            peak_working_set_bytes = Process.GetCurrentProcess().PeakWorkingSet64, phrases = results,
            source_notes = sourceNotes, phrase_layout_hash = input.TryGetProperty("phrase_layout_hash", out var hash) ? hash.GetString() : null,
            pitch_policy = "score pitch curves; no automatic pitch prediction or transposition", human_review = "unassessed"
        }, JsonOptions));
    }

    static void WriteFloatWav(string path, float[] samples) {
        using var writer = new BinaryWriter(File.Create(path));
        writer.Write("RIFF"u8); writer.Write(36 + samples.Length * 4); writer.Write("WAVEfmt "u8);
        writer.Write(16); writer.Write((short)3); writer.Write((short)1); writer.Write(44100); writer.Write(44100 * 4);
        writer.Write((short)4); writer.Write((short)32); writer.Write("data"u8); writer.Write(samples.Length * 4);
        foreach (var sample in samples) writer.Write(sample);
    }
}

// Pump OpenUtau's document callbacks on one thread without an Avalonia window.
internal sealed class PumpScheduler : TaskScheduler, IDisposable {
    readonly ConcurrentQueue<Task> tasks = new();
    readonly ConcurrentQueue<Action> actions = new();
    protected override IEnumerable<Task> GetScheduledTasks() => tasks.ToArray();
    protected override void QueueTask(Task task) => tasks.Enqueue(task);
    protected override bool TryExecuteTaskInline(Task task, bool wasQueued) => false;
    public void Post(Action action) => actions.Enqueue(action);
    public void Until(Func<bool> condition, TimeSpan timeout) {
        var clock = Stopwatch.StartNew();
        do {
            while (actions.TryDequeue(out var action)) action();
            while (tasks.TryDequeue(out var task)) { TryExecuteTask(task); if (task.IsFaulted) throw task.Exception!; }
            if (condition()) return;
            if (clock.Elapsed > timeout) throw new TimeoutException("OpenUtau document/render callback timed out.");
            Thread.Sleep(10);
        } while (true);
    }
    public void Dispose() { }
}
