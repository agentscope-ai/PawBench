import pandas as pd
import os
from collections import defaultdict
import re
import string
import random
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import AgglomerativeClustering
from nltk.corpus import stopwords
import nltk
nltk.download('stopwords')

# Standardize category text
def standardize_category(category):
    stop_words = set(stopwords.words('english'))
    return ' '.join([word.lower() for word in re.sub(f'[{string.punctuation}]', '', category).split() if word.lower() not in stop_words])

# Parse category path
def parse_category_path(path):
    return [standardize_category(cat) for cat in path.split(' > ')]

# Load data
def load_data(file_path):
    df = pd.read_csv(file_path)
    df['category_path'] = df['category_path'].apply(parse_category_path)
    return df

# Get categories
def get_categories(df):
    categories = defaultdict(list)
    for _, row in df.iterrows():
        for i, cat in enumerate(row['category_path']):
            categories[i].append(cat)
    return categories

# Find representative category
def find_representative_category(categories, threshold=0.7):
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(categories)
    similarity_matrix = cosine_similarity(tfidf_matrix, tfidf_matrix)
    representative = None
    max_similarity_sum = 0
    for i in range(len(categories)):
        similarity_sum = sum(similarity_matrix[i]) - 1  # Exclude self-similarity
        if similarity_sum / (len(categories) - 1) >= threshold:
            representative = categories[i]
            break
    return representative

# Build unified taxonomy
def build_unified_taxonomy(categories, depth=5, min_subcategories=3, max_subcategories=20, min_top_level=10, max_top_level=20, sibling_overlap_threshold=0.3):
    unified_taxonomy = {}
    for level in range(depth):
        if level == 0:  # Top level
            clustering = AgglomerativeClustering(n_clusters=random.randint(min_top_level, max_top_level), affinity='cosine', linkage='average')
            clustering.fit(vectorizer.transform(categories[0]))
            for i in range(clustering.n_clusters_):
                cluster_indices = [j for j, c in enumerate(clustering.labels_) if c == i]
                cluster_categories = [categories[0][j] for j in cluster_indices]
                representative = find_representative_category(cluster_categories)
                if representative:
                    unified_taxonomy[representative] = {}
                else:
                    unified_taxonomy[f'Cluster {i}'] = {}
        else:  # Deeper levels
            for parent, children in list(unified_taxonomy.items()):
                if len(children) == 0:
                    child_categories = [cat for cat_list in categories[level] for cat in cat_list]
                    if len(child_categories) > 0:
                        clustering = AgglomerativeClustering(n_clusters=min(max_subcategories, len(child_categories)), affinity='cosine', linkage='average')
                        clustering.fit(vectorizer.transform(child_categories))
                        for i in range(clustering.n_clusters_):
                            cluster_indices = [j for j, c in enumerate(clustering.labels_) if c == i]
                            cluster_categories = [child_categories[j] for j in cluster_indices]
                            representative = find_representative_category(cluster_categories)
                            if representative:
                                children[representative] = {}
    return unified_taxonomy

# Flatten taxonomy
def flatten_taxonomy(taxonomy, current_path=[], flat_taxonomy=[]):
    for key, value in taxonomy.items():
        new_path = current_path + [key]
        flat_taxonomy.append(new_path)
        if value:
            flatten_taxonomy(value, new_path, flat_taxonomy)
    return flat_taxonomy

# Write output
def write_output(taxonomy, output_dir):
    flat_taxonomy = flatten_taxonomy(taxonomy)
    full_df = pd.DataFrame(columns=['source', 'category_path', 'depth', 'unified_level_1', 'unified_level_2', 'unified_level_3', 'unified_level_4', 'unified_level_5'])
    hierarchy_df = pd.DataFrame(columns=['unified_level_1', 'unified_level_2', 'unified_level_3', 'unified_level_4', 'unified_level_5'])
    for i, path in enumerate(flat_taxonomy):
        for source, df in dfs.items():
            for _, row in df.iterrows():
                if all(standardized_cat in path for standardized_cat in row['category_path']):
                    full_df = full_df.append({'source': source, 'category_path': ' > '.join(row['category_path']), 'depth': len(row['category_path']), **{f'unified_level_{j+1}': path[j] if j < len(path) else '' for j in range(5)}}, ignore_index=True)
                    hierarchy_df = hierarchy_df.append({f'unified_level_{j+1}': path[j] if j < len(path) else '' for j in range(5)}, ignore_index=True)
    full_df.to_csv(os.path.join(output_dir, 'unified_taxonomy_full.csv'), index=False)
    hierarchy_df.to_csv(os.path.join(output_dir, 'unified_taxonomy_hierarchy.csv'), index=False)

# Load data
dfs = {os.path.basename(file_path): load_data(file_path) for file_path in glob.glob('data/*.csv')}

# Get categories
categories = get_categories(pd.concat(dfs.values()))

# Build unified taxonomy
unified_taxonomy = build_unified_taxonomy(categories)

# Write output
write_output(unified_taxonomy, 'output')
